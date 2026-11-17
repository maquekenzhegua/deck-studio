"""CLI.

    deck facts <dir>            列出算出来的事实（不花 token，先看数对不对）
    deck build <dir> -o a.pptx  出片
    deck plan  <dir>            只出大纲 JSON，不出片

The `facts` subcommand exists because the first question anyone asks about a
generated deck is "where did this number come from". You should be able to
answer it before spending a single token.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import compute, ingest, plan as planner, render, verify
from .llm import Client, LLMError


def _load(path: str):
    b = ingest.load(path)
    if b.skipped:
        print(f"跳过 {len(b.skipped)} 个文件：{', '.join(b.skipped[:6])}", file=sys.stderr)
    print(f"读到 {b.summary()}", file=sys.stderr)
    return b


def cmd_facts(args) -> int:
    b = _load(args.input)
    facts = compute.extract(b)
    if not facts:
        print("没有算出任何事实。检查表格里是否有数值列。", file=sys.stderr)
        return 1
    print(f"\n共 {len(facts)} 条事实，按信息量排序：\n")
    for f in facts[: args.top]:
        bar = "█" * max(1, int(f.score * 4))
        print(f"{f.id:>4}  {f.kind:<9} {f.label[:34]:<34} {f.value:>12}  {bar}")
        print(f"      {f.source}")
    return 0


def cmd_plan(args) -> int:
    b = _load(args.input)
    facts = compute.pick(compute.extract(b), args.slides)
    client = Client(model=args.model, base_url=args.base_url)
    p = planner.make(b, facts, args.ask, client=client, slides=args.slides, deck_title=args.title)
    print(p.to_json())
    print(f"\n用量：{client.usage.total} tokens", file=sys.stderr)
    return 0


def cmd_build(args) -> int:
    b = _load(args.input)
    all_facts = compute.extract(b)
    if not all_facts:
        print("没有算出任何事实：输入里需要有带数值列的表格。", file=sys.stderr)
        return 1
    facts = compute.pick(all_facts, args.slides)

    print("\n引用的事实：", file=sys.stderr)
    for f in facts:
        print(f"  {f.id}  {f.label} = {f.value}", file=sys.stderr)

    client = Client(model=args.model, base_url=args.base_url)
    try:
        p = planner.make(b, facts, args.ask, client=client, slides=args.slides, deck_title=args.title)
    except (LLMError, ValueError) as e:
        print(f"规划失败：{e}", file=sys.stderr)
        return 2

    print(f"\n大纲（{len(p.slides)} 页）：", file=sys.stderr)
    for i, s in enumerate(p.slides, 1):
        print(f"  {i}. [{s.layout}] {s.title}", file=sys.stderr)

    problems = verify.check(p, facts)
    if problems:
        print("\n校验发现问题：", file=sys.stderr)
        print(verify.report(problems), file=sys.stderr)
        if not args.force:
            print("\n加 --force 仍然出片（不推荐：有数字找不到来源）", file=sys.stderr)
            return 3
    else:
        print("\n✓ 数字校验通过", file=sys.stderr)

    out = render.build(p, facts, b.pictures, args.out)
    Path(args.out).with_suffix(".plan.json").write_text(p.to_json(), encoding="utf-8")
    print(f"\n已生成 {out}", file=sys.stderr)
    print(f"用量：{client.usage.total} tokens", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deck", description="把工作文件变成一份不会说谎的汇报")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("facts", help="列出算出来的事实")
    f.add_argument("input")
    f.add_argument("--top", type=int, default=20)
    f.set_defaults(func=cmd_facts)

    p = sub.add_parser("plan", help="只出大纲")
    p.add_argument("input")
    p.add_argument("--ask", default="")
    p.add_argument("--slides", type=int, default=6)
    p.add_argument("--model", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--title", default=None)
    p.set_defaults(func=cmd_plan)

    b = sub.add_parser("build", help="出片")
    b.add_argument("input")
    b.add_argument("-o", "--out", default="out/deck.pptx")
    b.add_argument("--ask", default="")
    b.add_argument("--slides", type=int, default=6)
    b.add_argument("--model", default=None)
    b.add_argument("--base-url", default=None)
    b.add_argument("--title", default=None)
    b.add_argument("--force", action="store_true", help="校验失败也出片")
    b.set_defaults(func=cmd_build)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

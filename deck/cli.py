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
import json
import sys
import tempfile
from pathlib import Path

from . import brief as briefing
from . import compose
from . import agent, compute, ingest, multimodal, plan as planner, render, render2, review, verify
from .llm import Client, LLMError

# A Windows console defaults to GBK, and the check marks and Chinese quotes in
# this CLI are not encodable there. Replace rather than crash: a mojibake line
# is a nuisance, a traceback at the end of a two-minute build is a lost run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _load(path: str):
    b = ingest.load(path)
    # Classify pictures and pull embedded media, exactly like the desktop app;
    # otherwise a chart is rendered as a photo and the caption has no kind.
    multimodal.augment(b, Path(tempfile.gettempdir()) / "deck-media")
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


def _answers(raw: str | None) -> dict[str, str]:
    """`--answer audience=管理层,decision=批预算` — no interactive prompt.

    Deliberately not a TTY dialogue: the useful place to answer is a script or
    a rerun after reading the questions, not a terminal waiting for Enter.
    """
    out: dict[str, str] = {}
    for part in (raw or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def _make_brief(b, facts, args, client) -> "briefing.Brief":
    br = briefing.make(b, facts, getattr(args, "ask", "") or "", client=client,
                       answers=_answers(getattr(args, "answer", None)))
    return br


def _print_brief(br) -> None:
    print(f"\n【需求判断】置信度 {br.confidence:.0%} · {br.mode}", file=sys.stderr)
    for k in briefing.SLOTS:
        s = br.get(k)
        if not s.value:
            print(f"  {briefing.LABEL[k]:<5} — 未知", file=sys.stderr)
            continue
        mark = "?" if s.assumed else " "
        ev = f"  ← {s.evidence}" if s.evidence else ""
        print(f"  {mark}{briefing.LABEL[k]:<5} {s.value}{ev}", file=sys.stderr)
    if br.questions:
        print("\n需要你确认（不确定就先跳过，会按默认走）：", file=sys.stderr)
        for q in br.questions:
            print(f"\n  [{q.id}] {q.ask}", file=sys.stderr)
            for i, o in enumerate(q.options, 1):
                print(f"      {i}) {o}", file=sys.stderr)
            print(f"      默认：{q.default}", file=sys.stderr)
            print(f"      为什么问：{q.why}", file=sys.stderr)
        print("\n  回答方式：--answer \"" + ",".join(f"{q.id}=…" for q in br.questions) + "\"",
              file=sys.stderr)


def cmd_brief(args) -> int:
    b = _load(args.input)
    facts = compute.extract(b)
    client = Client(model=args.model, base_url=args.base_url)
    br = _make_brief(b, facts, args, client)
    _print_brief(br)
    if args.out:
        Path(args.out).write_text(br.to_json(), encoding="utf-8")
        print(f"\n已写出 {args.out}", file=sys.stderr)
    print(f"\n用量：{client.usage.total} tokens", file=sys.stderr)
    return 0


def cmd_plan(args) -> int:
    b = _load(args.input)
    facts = compute.pick(compute.extract(b), args.slides)
    if getattr(args, "offline", False):
        p = compose.draft(b, facts, deck_title=args.title or "")
        print(p.to_json())
        return 0
    client = Client(model=args.model, base_url=args.base_url)
    br = _make_brief(b, facts, args, client)
    _print_brief(br)
    p = planner.make(b, facts, args.ask, client=client, slides=args.slides,
                     deck_title=args.title, brief=br)
    print(p.to_json())
    print(f"\n用量：{client.usage.total} tokens", file=sys.stderr)
    return 0


def _render_only(args, plan, bundle, facts) -> int:
    """Write a deck whose argument came from the material, not from a table."""
    print(f"\n大纲（{len(plan.slides)} 页）：", file=sys.stderr)
    for i, s in enumerate(plan.slides, 1):
        print(f"  {i}. [{s.layout}] {s.title}", file=sys.stderr)
    out = render2.build(plan, facts, bundle.pictures, args.out, theme=args.theme)
    Path(args.out).with_suffix(".plan.json").write_text(plan.to_json(), encoding="utf-8")
    print(f"\n已生成 {out}", file=sys.stderr)
    return 0


def cmd_build(args) -> int:
    b = _load(args.input)
    all_facts = compute.extract(b)
    if not all_facts:
        # No table, no chart, no KPI. A folder of documents still has something
        # to say, and a folder of pictures still has something to show; only an
        # empty folder has neither.
        if b.docs:
            p = compose.draft_text(b)
            print("\n没有数据表：按文档原文出稿。", file=sys.stderr)
            return _render_only(args, p, b, all_facts)
        if b.pictures:
            p = compose.draft_pictures(b)
            print("\n没有数据表：按图片材料出稿。", file=sys.stderr)
            return _render_only(args, p, b, all_facts)
        print("没有可用的材料：既没有表格，也没有正文和图片。", file=sys.stderr)
        return 1
    facts = all_facts if args.offline else compute.pick(all_facts, args.slides)

    print("\n引用的事实：", file=sys.stderr)
    for f in facts:
        print(f"  {f.id}  {f.label} = {f.value}", file=sys.stderr)

    br = None
    if args.offline:
        # No endpoint, no key, no network: write the argument from the facts.
        p = compose.draft(b, facts, deck_title=args.title or "")
        print("\n离线规划：标题与取舍全部来自已计算事实。", file=sys.stderr)
    else:
        client = Client(model=args.model, base_url=args.base_url)
        try:
            br = _make_brief(b, facts, args, client)
            _print_brief(br)
            # Below 30% we do not know what this deck is for. Building anyway means
            # guessing the audience, and a deck written for the wrong room cannot be
            # fixed by editing it. Stop and let the questions be answered.
            if br.mode == "blocked" and not (args.force or args.answer):
                print("\n需求判断置信度过低，无法确定受众与决策诉求。"
                      "\n先回答上面的问题（--answer），或加 --force 按默认假设出片。",
                      file=sys.stderr)
                return 4
            p = planner.make(b, facts, args.ask, client=client, slides=args.slides,
                             deck_title=args.title, brief=br)
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

    engine = render if args.classic else render2
    out = engine.build(p, facts, b.pictures, args.out, **({} if args.classic else {"theme": args.theme}))
    Path(args.out).with_suffix(".plan.json").write_text(p.to_json(), encoding="utf-8")
    if br:
        Path(args.out).with_suffix(".brief.json").write_text(br.to_json(), encoding="utf-8")
    print(f"\n已生成 {out}", file=sys.stderr)
    if not args.offline:
        print(f"用量：{client.usage.total} tokens", file=sys.stderr)
    return 0


def cmd_review(args) -> int:
    b = _load(args.input)
    facts = compute.extract(b)
    p = planner.Plan.from_json(Path(args.plan).read_text(encoding="utf-8"))
    findings = review.review(p, facts)
    if args.json:
        from dataclasses import asdict
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        print(review.summary(findings))
        for f in findings:
            print(f"  第 {f.page} 页  [{f.level}]  {f.text}")
    return 0


def cmd_notes(args) -> int:
    b = _load(args.input)
    facts = compute.extract(b)
    p = planner.Plan.from_json(Path(args.plan).read_text(encoding="utf-8"))
    client = Client(model=args.model, base_url=args.base_url)
    got = agent.notes(p, facts, client=client)
    if got is None:
        print("模型这次没写出可用的讲稿（讲稿不许带数字）", file=sys.stderr)
        return 1
    new_plan, message = got
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(new_plan.to_json(), encoding="utf-8")
    print(f"{message} → {out}")
    print(f"用量：{client.usage.total} tokens", file=sys.stderr)
    return 0


def cmd_audit(args) -> int:
    """Design-rule gate. Separate from `verify` on purpose: verify asks whether the
    deck lies, audit asks whether it is readable."""
    from . import audit as A

    return A.cli_report(args.pptx, args.plan, args.json)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deck", description="把工作文件变成一份不会说谎的汇报")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("facts", help="列出算出来的事实")
    f.add_argument("input")
    f.add_argument("--top", type=int, default=20)
    f.set_defaults(func=cmd_facts)

    br = sub.add_parser("brief", help="先判断这份 deck 该是什么，必要时向你提问")
    br.add_argument("input")
    br.add_argument("--ask", default="")
    br.add_argument("--answer", default=None, help='Q_audience=管理层,Q_decision=批预算')
    br.add_argument("--model", default=None)
    br.add_argument("--base-url", default=None)
    br.add_argument("-o", "--out", default=None)
    br.set_defaults(func=cmd_brief)

    p = sub.add_parser("plan", help="只出大纲")
    p.add_argument("input")
    p.add_argument("--ask", default="")
    p.add_argument("--answer", default=None)
    p.add_argument("--slides", type=int, default=6)
    p.add_argument("--model", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--title", default=None)
    p.add_argument("--offline", action="store_true", help="不调用模型，按事实离线成稿")
    p.set_defaults(func=cmd_plan)

    b = sub.add_parser("build", help="出片")
    b.add_argument("input")
    b.add_argument("-o", "--out", default="out/deck.pptx")
    b.add_argument("--ask", default="")
    b.add_argument("--answer", default=None, help='Q_audience=管理层,Q_decision=批预算')
    b.add_argument("--slides", type=int, default=6)
    b.add_argument("--model", default=None)
    b.add_argument("--base-url", default=None)
    b.add_argument("--title", default=None)
    b.add_argument("--force", action="store_true", help="校验失败也出片")
    b.add_argument("--theme", default="navy",
                   help="视觉主题：navy / editorial / tech / forest / midnight")
    b.add_argument("--classic", action="store_true", help="用旧版渲染器（v1）")
    b.add_argument("--offline", action="store_true", help="不调用模型，按事实离线成稿")
    b.set_defaults(func=cmd_build)

    rv = sub.add_parser("review", help="像编辑一样读大纲，指出问题（不花 token）")
    rv.add_argument("input")
    rv.add_argument("--plan", required=True, help="plan.json")
    rv.add_argument("--json", action="store_true")
    rv.set_defaults(func=cmd_review)

    nt = sub.add_parser("notes", help="让模型给每页写一句讲稿")
    nt.add_argument("input")
    nt.add_argument("--plan", required=True, help="plan.json")
    nt.add_argument("-o", "--out", default="out/plan-notes.json")
    nt.add_argument("--model", default=None)
    nt.add_argument("--base-url", default=None)
    nt.set_defaults(func=cmd_notes)

    a = sub.add_parser("audit", help="量这份 pptx 是否符合设计规则（不花 token）")
    a.add_argument("pptx")
    a.add_argument("--plan", default=None, help="plan.json，用来判断版式节奏")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_audit)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

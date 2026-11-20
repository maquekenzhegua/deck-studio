"""Let the model lead, with the program holding the pen.

The offline planner (`compose.py`) can order a deck from the data alone, and it
will never invent a number — but it also never notices that two slides repeat
the same point, or that a page would be stronger as a split image. A model
notices. What it must not do is write figures.

So the division of labour is:

* the model decides the argument: page order, which fact earns a page, what
  each title claims, where prose is better than a chart;
* `verify.py` refuses any deck whose numbers do not trace to a fact;
* `audit`/`verify` findings are fed *back* to the model once, in a repair
  pass, instead of being silently repaired by us — that is the difference
  between a model-driven deck and a template with model-flavoured words;
* if the model is absent, slow, or produces nothing usable, `compose.py` takes
  over and the result says so. A deck that renders is worth more than a
  principled failure.

The log is returned for the UI: "模型主导 · 2 次修订" is a claim the user can
check, so it has to be produced by counting what actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import compose, verify
from .compute import Fact
from .ingest import Bundle
from .llm import Client, LLMError
from .plan import Plan, _validate, make as model_make

REPAIR_SYSTEM = """你是一位汇报顾问，正在修订自己刚写的大纲。下面列出机器校验发现的问题。
只修这些问题，不要重写已经成立的部分；页数、页序、已通过的标题尽量保留。

规则不变：
1. 不许写任何数字，需要数字写 {{f3}} 这样的占位符（只能引用给到的事实 id）。
2. 标题必须是判断句；每页一个论点；要点最多 3 条，每条不超过 28 个汉字。
3. 版式只能从给定清单里选；chart/table/key_figure/kpi_row 必须绑定事实 id。
4. 不要为了通过校验而删掉关键论点——如果某页无法修好，换成能成立的版式。

只输出修订后的完整 JSON，结构与你上次输出一致。"""


@dataclass
class AgentResult:
    plan: Plan
    planner: str = "offline"          # "model" | "offline"
    model: str = ""
    revisions: int = 0
    log: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.planner == "offline":
            return "离线规划"
        tail = f" · 修订 {self.revisions} 次" if self.revisions else ""
        return f"{self.model or '模型'} 主导{tail}"


def _issues(plan: Plan, facts: list[Fact]) -> list[str]:
    out = [f"第 {p.slide} 页：{p.detail}" for p in verify.check(plan, facts)]
    layouts = [s.layout for s in plan.slides]
    # Two identical layouts in a row is the visual equivalent of the same
    # sentence twice; it is not fatal, so it is reported rather than blocked.
    for i in range(2, len(layouts)):
        if layouts[i] == layouts[i - 1] == layouts[i - 2]:
            out.append(f"第 {i + 1} 页：连续三页都是 {layouts[i]}，换一种版式")
    if layouts and layouts[0] != "title":
        out.append("第 1 页必须是 title")
    if layouts and layouts[-1] != "closing":
        out.append("最后一页必须是 closing")
    return out


def _repair(client: Client, plan: Plan, issues: list[str], facts: list[Fact],
            bundle: Bundle, deck_title: str | None) -> Plan | None:
    payload = plan.to_json()
    prompt = (
        "## 机器校验发现的问题\n"
        + "\n".join(f"- {line}" for line in issues[:12])
        + "\n\n## 当前大纲 JSON\n"
        + payload
        + "\n\n## 事实清单（只能引用这些 id）\n"
        + "\n".join(f"{f.id}: {f.label} = {f.value}" for f in facts[:40])
    )
    try:
        data = client.json(REPAIR_SYSTEM, prompt, temperature=0.15, max_tokens=2600)
        revised = _validate(data, facts, bundle.pictures, deck_title)
    except (LLMError, ValueError, KeyError):
        return None
    return revised


def plan(bundle: Bundle, facts: list[Fact], ask: str = "", *, slides: int = 0,
         deck_title: str | None = None, subtitle: str = "",
         client: Client | None = None, revisions: int = 1) -> AgentResult:
    """Model first, program as the fallback and the referee."""
    if not facts:
        return AgentResult(plan=compose.draft_text(bundle), planner="offline",
                           log=["没有数据表，按文档出稿"])

    fallback = compose.draft(bundle, facts, deck_title=deck_title, subtitle=subtitle)
    if client is None:
        return AgentResult(plan=fallback, planner="offline", log=["未配置模型，使用离线规划"])

    want = slides or min(12, 6 + len(facts) // 4)
    log: list[str] = []
    try:
        plan_model = model_make(bundle, facts, ask, client=client, slides=want,
                                deck_title=deck_title)
        if subtitle:
            plan_model.subtitle = subtitle
        log.append(f"模型出稿 {len(plan_model.slides)} 页")
    except (LLMError, ValueError) as exc:
        log.append(f"模型不可用：{str(exc)[:120]}")
        return AgentResult(plan=fallback, planner="offline", model=client.model, log=log)

    issues = _issues(plan_model, facts)
    used = 0
    while issues and used < revisions:
        revised = _repair(client, plan_model, issues, facts, bundle, deck_title)
        if revised is None:
            log.append("修订失败，保留模型原稿")
            break
        used += 1
        after = _issues(revised, facts)
        log.append(f"第 {used} 次修订：问题 {len(issues)} → {len(after)}")
        if len(after) >= len(issues):
            # The repair made it no better; keep the version the program can
            # still defend rather than the one that merely looks newer.
            break
        plan_model, issues = revised, after

    # Numbers are the one thing that cannot ship wrong; a repeated layout is a
    # style note. Falling back over a style note would throw away the model's
    # editorial judgement -- the exact thing it was hired for.
    # "style" and "overflow" are notes the renderer already handles; only an
    # untraceable figure or a chart with nothing behind it blocks delivery.
    blockers = [p for p in verify.check(plan_model, facts)
                if p.kind in ("number", "structure")]
    if blockers:
        log.append("仍有数字对不上，退回离线规划以保证可交付")
        return AgentResult(plan=fallback, planner="offline", model=client.model,
                           revisions=used, log=log,
                           problems=[f"第 {p.slide} 页：{p.detail}" for p in blockers][:6])
    if issues:
        log.append(f"保留模型稿，{len(issues)} 条版式建议未处理")

    return AgentResult(plan=plan_model, planner="model", model=client.model,
                       revisions=used, log=log)


REVISE_SYSTEM = """你是一位汇报顾问，正在按用户的一句话要求修改自己的大纲。
只改用户点到的地方；没被提到的页面、页序、标题原样保留。

规则不变：
1. 不许写任何数字，需要数字就写 {{f3}} 这样的占位符（只能引用给到的事实 id）。
2. 标题必须是判断句；每页一个论点；要点最多 3 条，每条不超过 28 个汉字。
3. 版式只能从给定清单里选；chart/table/key_figure/kpi_row 必须绑定事实 id。
4. 输出与输入同构的 JSON：{"deck_title": "...", "subtitle": "...", "slides": [...]}，
   每页字段：layout / title / fact / image / note / bullets / variant。
"""


def revise(current: Plan, facts: list[Fact], text: str, *, client: Client,
           pictures: list | None = None) -> tuple[Plan, str] | None:
    """Let the model take a one-line instruction and rewrite the outline.

    Returns None when the model is unusable or its answer does not survive
    validation, so the caller can fall back to the rule-based editor. The
    rules still own numbers: a revision that invents a figure is rejected
    whole rather than shipped partially.
    """
    if not text.strip():
        return None
    prompt = (
        "## 用户的要求\n" + text.strip()
        + "\n\n## 当前大纲 JSON\n" + current.to_json()
        + "\n\n## 事实清单（只能引用这些 id）\n"
        + "\n".join(f"{f.id}: {f.label} = {f.value}" for f in facts[:40])
        + "\n\n## 可用版式\n" + ", ".join(sorted(_layouts()))
    )
    try:
        data = client.json(REVISE_SYSTEM, prompt, temperature=0.2, max_tokens=3000)
        revised = _validate(data, facts, pictures or [], None)
    except (LLMError, ValueError, KeyError):
        return None
    if not revised.slides:
        return None
    # Numbers are still the program's call: a revision that invents a figure
    # is rejected whole, not shipped with one bad page.
    bad = [p for p in verify.check(revised, facts) if p.kind in ("number", "structure")]
    if bad:
        return None
    revised.deck_title = revised.deck_title or current.deck_title
    revised.subtitle = revised.subtitle or current.subtitle
    return revised, f"模型改稿：{len(current.slides)} → {len(revised.slides)} 页"


def _layouts() -> list[str]:
    from .plan import LAYOUTS
    return sorted(LAYOUTS)

"""Plan: the model writes the storyline, never the numbers.

Two rules make this safe to run on a cheap model:

1. The planner may only cite a fact by id (`{{f3}}`), never write a figure.
   Substitution happens in the renderer. A model that cannot emit a digit
   cannot emit a wrong digit.
2. The slide order is fixed by a narrative template, not left to the model:
   conclusion first, then evidence, then what to do. Left alone, models produce
   a table of contents disguised as slides.

Everything the model returns is validated against the fact list before render,
so a hallucinated id is an error, not a broken slide.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .compute import Fact
from .ingest import Bundle, Doc, Picture
from .llm import Client

LAYOUTS = {
    "title", "section", "key_figure", "kpi_row", "chart", "table",
    "bullets", "image_full", "split_image", "closing", "quote", "notes", "compare",
}

# Titles that are noun phrases. "销售情况分析" tells the reader nothing; the
# slide underneath it could say anything. A deck of these is the single
# clearest sign that a model wrote it, because a human who knows the answer
# writes the answer in the title.
NOUNISH = re.compile(
    r"(分析|概述|情况|介绍|总结|汇报|说明|展望|回顾|探讨|研究|概览|简述)$"
)

SYSTEM = """标题必须是一句可读的判断（例如「华东撑起三分之一收入」），不要只写占位符或数字。\nJSON 尽量紧凑：标题不超过 22 字，note 不超过 34 字，不要多余空白。
你是一位给中国公司做汇报的资深顾问。你写的不是"PPT"，是一份让人在 90 秒内做决定的材料。

硬性规则，违反任何一条即为不合格：
1. 你绝对不能自己写任何数字。需要数字时写占位符 {{f3}}（f3 是给定事实的 id）。
   唯一允许你直接写的数字是年份（如 2024）和序号。
2. 每页只讲一个论点。标题必须是完整的判断句——有主语、有谓语、有结论。
   ❌ "销售情况分析"  ❌ "各区域表现"  ❌ "问题与挑战"
   ✅ "华东一个区贡献了全年六成增长"  ✅ "东北在下滑，但客户数还在涨"
3. 把全部标题从上到下连起来读，应该是一篇能独立读懂的文章。
   如果标题串起来只是目录，说明你没有在讲结论，重写。
4. 要点不超过 3 条，每条不超过 28 个汉字。写不完就拆页，不要缩字号。
5. 禁止出现："本页介绍了""综上所述""随着…的发展""值得关注的是""首先/其次/最后"。
6. 量词必须成立。用「唯一」「全部」「均」「只有」之前先看事实里的 signs 字段，
   它写着几项为正、几项为负。两项都在跌的时候写「唯一下滑」是假话，
   即使每个数字都对。拿不准就写具体名字，不写量词。
7. 叙事顺序固定：先给结论，再给证据，最后给行动。不要目录页，不要致谢页。
8. 同一条事实最多支撑两页。不要把同一条事实先画成图，再用一张同数据的图占一页。
9. table 可以绑定两个同维度的事实（fact 写 "f13,f17"），第二列会自动并排显示；当你要说「A 第一、B 第五」这类比较时，必须把两个事实都绑上，否则读者只能看到一半。

版式只能从这些里选：
- title       封面。大标题写这份汇报的结论，不是"XX年度汇报"这种标签
- key_figure  整页只讲一个数字 + 一句解释。用于最重要的那个发现
- kpi_row     3–4 个数字并排，用于一组同量级的指标。fact 写成 "f1,f2,f3"
- chart       图表 + 最多 3 条要点。必须绑定一个 series/breakdown 事实。
              可选 variant 决定画法：
              · "multi"   一条时间轴上按类别分面（"multi"），适合 2–4 条产品线；
              · "donut"   占比环图，适合 3–5 项的构成，且总量本身有意义；
              · "heatmap" 记分卡，fact 写多个同维度事实 "f10,f12"，用于多维对比；
              · "waterfall" 增量桥图，fact 写「规模事实,增速事实」"f10,f12"，回答增量从哪来；
              不写 variant 就是普通条形/折线图。
- table       明细表，用于需要逐项对照的排名或构成。必须绑定一个 breakdown 事实；
              要并排看两个指标时 fact 写 "f13,f17"，第二列自动出现
- compare     两栏对照，左边写还在涨的、右边写在退的。fact 写两个事实 "f15,f12"
- quote       引用客户或文档原话，标题就是那句原话
- notes       原文摘录，用于没有数字的文档
- bullets     纯要点页，用于没有数据的判断
- image_full  整页一张图 + 一句说明。必须绑定一张图
- split_image 左图右文，用于图文结合
- section     章节分隔页。整份最多用 2 次
- closing     结尾，给行动建议

只输出 JSON，不要任何解释文字。"""

USER_TMPL = """## 这份 deck 是什么
{brief_block}

## 用户的原话（只作参考，上面那段更准）
{ask}

## 我手上有什么
{inventory}

## 事实清单（你只能引用这些 id，不能写数字）
{facts}

## 材料覆盖度（监理给你的边界，别越界）
{coverage}

## 可用的图片
{pictures}

## 文档摘录
{docs}

{outline}

## 任务
写一份 {n} 页的汇报。结构：
1. title（封面，标题就是整份材料的一句话结论）
2. key_figure 或 kpi_row —— 最重要的那个发现，结论先行
3. {body} 页证据，每页一个论点，优先用 chart / table / split_image
4. closing（行动建议，2–3 条，每条是一个动词开头的动作）

输出这个 JSON：
{{
  "deck_title": "不超过 20 字",
  "subtitle": "不超过 30 字。必须是这份材料自己的判断，不是复述用户的要求",
  "slides": [
    {{"layout": "key_figure", "title": "论点句", "fact": "f3", "note": "一句话解释这个数字意味着什么"}},
    {{"layout": "kpi_row", "title": "论点句", "fact": "f1,f2,f5", "note": "一句话"}},
    {{"layout": "chart", "title": "论点句", "fact": "f7", "bullets": ["要点一", "要点二"]}},
    {{"layout": "table", "title": "论点句", "fact": "f9"}},
    {{"layout": "split_image", "title": "论点句", "image": "p1", "note": "一句说明"}},
    {{"layout": "bullets", "title": "论点句", "bullets": ["要点一", "要点二", "要点三"]}},
    {{"layout": "closing", "title": "接下来做什么", "bullets": ["行动一", "行动二"]}}
  ]
}}"""


@dataclass
class Slide:
    layout: str
    title: str
    fact: str | None = None
    image: str | None = None
    note: str = ""
    bullets: list[str] = field(default_factory=list)
    # Optional rendering variant for a layout (chart: donut / heatmap / multi).
    # The layout stays "chart" so the rest of the pipeline -- verify, audit,
    # the UI dropdown -- keeps treating it as one page with a chart on it.
    variant: str = ""
    # What the presenter says out loud. Written by the model on request, kept
    # separate from `note` (which is printed on the page).
    script: str = ""


@dataclass
class Plan:
    deck_title: str
    subtitle: str
    slides: list[Slide]

    def to_json(self) -> str:
        return json.dumps(
            {
                "deck_title": self.deck_title,
                "subtitle": self.subtitle,
                "slides": [s.__dict__ for s in self.slides],
            },
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> "Plan":
        """Rebuild from to_json output.

        Exists so a plan can be edited as JSON and re-rendered without paying
        for another model call — the whole point of separating planning from
        rendering.
        """
        data = json.loads(raw)
        slides = [
            Slide(
                layout=str(s.get("layout") or "bullets"),
                title=str(s.get("title") or ""),
                fact=s.get("fact") or None,
                image=s.get("image") or None,
                note=str(s.get("note") or ""),
                bullets=[str(b) for b in (s.get("bullets") or [])],
                variant=str(s.get("variant") or ""),
            )
            for s in data.get("slides") or []
        ]
        return cls(
            deck_title=str(data.get("deck_title") or "汇报"),
            subtitle=str(data.get("subtitle") or ""),
            slides=slides,
        )


def _inventory(b: Bundle) -> str:
    lines = []
    for t in b.tables:
        cols = "、".join(str(c) for c in t.df.columns[:12])
        lines.append(f"- 表《{t.name}》{len(t.df)} 行，列：{cols}")
    for d in b.docs:
        lines.append(f"- 文档《{d.name}》{len(d.text)} 字")
    if b.pictures:
        lines.append(f"- 图片 {len(b.pictures)} 张")
    return "\n".join(lines) or "（没有结构化数据）"


def _facts_block(facts: list[Fact]) -> str:
    return "\n".join(json.dumps(f.as_prompt(), ensure_ascii=False) for f in facts)


def _pictures_block(pics: list[Picture]) -> str:
    if not pics:
        return "（没有图片）"
    return "\n".join(
        f"- {p.id}《{p.name}》{p.width}×{p.height}" + (f" 内容：{p.caption}" if p.caption else "")
        for p in pics
    )


def _docs_block(docs: list[Doc], limit: int = 3000) -> str:
    if not docs:
        return "（没有文档）"
    out, used = [], 0
    for d in docs:
        chunk = d.text[: max(0, limit - used)]
        if not chunk:
            break
        out.append(f"《{d.name}》\n{chunk}")
        used += len(chunk)
    return "\n\n".join(out)


def make(
    bundle: Bundle,
    facts: list[Fact],
    ask: str,
    *,
    client: Client,
    slides: int = 6,
    deck_title: str | None = None,
    brief=None,
) -> Plan:
    if not facts:
        raise ValueError("没有可引用的事实：先确保输入里有带数值列的表格")
    body = max(2, slides - 3)
    # Without a brief there is no audience, and the old fallback ("给管理层看的
    # 汇报") is exactly the silent assumption this module exists to kill: it
    # was never declared, so it could never be corrected. Now an absent brief
    # produces an explicit "unknown" line the model has to reason around.
    brief_block = brief.block() if brief is not None else (
        "【这份 deck 是什么】\n- 未提供：受众与决策诉求都不清楚，"
        "请只写能从数据本身得出的结论，不要假设读者是谁。")
    user = USER_TMPL.format(
        brief_block=brief_block,
        ask=ask or "（没说）",
        inventory=_inventory(bundle),
        facts=_facts_block(facts),
        pictures=_pictures_block(bundle.pictures),
        docs=_docs_block(bundle.docs),
        outline=bundle.outline_block() or "",
        n=slides,
        body=body,
        coverage=_coverage(bundle, facts),
    )
    data = client.json(SYSTEM, user, temperature=0.25, max_tokens=2600)
    return _validate(data, facts, bundle.pictures, deck_title,
                     (brief.get('decision').value if brief is not None else ask))


def _coverage(bundle: Bundle, facts: list[Fact]) -> str:
    """Tell the model which arguments the material can actually carry.

    Without this the model happily writes a five-act strategy story on top of
    one column of monthly revenue. Its plan is then either unsupported or
    padded; both lose to a plan that knows what is missing.
    """
    try:
        from .readiness import audit
    except Exception:  # noqa: BLE001
        return ""
    rd = audit(bundle, facts)
    gaps = [c.label for c in rd.checks if not c.have]
    if not gaps:
        return "- 材料覆盖：趋势 / 结构 / 同比 / 漏斗 / 图片 / 原话 都有。"
    return "- 材料覆盖：缺少 " + "、".join(gaps) + "。不要为缺的材料编页；缺少的部分用一句话点明，不要造数。"


def _norm(s: str) -> str:
    return re.sub(r"[\s，。、：；！？,.:;!?\"'“”‘’（）()【】\[\]]", "", s)


def _echo(sub: str, ask: str) -> bool:
    """True when the subtitle is just the user's request read back.

    A model asked for a subtitle will happily paste the ask. That single line
    is the most visible tell that nobody wrote the deck, so it is checked
    rather than trusted.
    """
    if not sub or not ask:
        return False
    a, b = _norm(sub), _norm(ask)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() > 0.55


def is_noun_phrase(title: str) -> bool:
    """A title ending in 分析/情况/概述 is a label, not a claim."""
    t = _norm(title)
    return bool(t) and bool(NOUNISH.search(t))


def _validate(data: dict, facts: list[Fact], pics: list[Picture],
              deck_title: str | None, ask: str = "") -> Plan:
    fact_ids = {f.id for f in facts}
    pic_ids = {p.id for p in pics}
    slides: list[Slide] = []

    for raw in data.get("slides") or []:
        layout = str(raw.get("layout") or "").strip()
        if layout not in LAYOUTS:
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        fact = raw.get("fact")
        image = raw.get("image")
        variant = str(raw.get("variant") or "").strip()
        if variant not in ("", "multi", "donut", "heatmap", "waterfall"):
            variant = ""

        if layout in ("chart", "key_figure", "table", "kpi_row") and fact:
            # A multi-fact slide names its facts "f1,f2". Keep only the ids we
            # actually have, and drop the slide if none survive — rendering a
            # chart with no data behind it is the one thing this project
            # exists to prevent.
            keep = [p.strip() for p in str(fact).split(",") if p.strip() in fact_ids]
            if not keep:
                continue
            fact = ",".join(keep)
            # A funnel's message is its step conversion, and a bar chart of the
            # absolute counts cannot show that -- it says "everything shrinks",
            # which is what the reader already assumed. Route it to the table,
            # which prints the conversion per stage.
            if layout == "chart" and len(keep) == 1:
                one = next((f for f in facts if f.id == keep[0]), None)
                if one is not None and one.chart == "funnel":
                    layout = "table"
        elif layout in ("chart", "key_figure", "table", "kpi_row"):
            if layout == "chart":
                continue
            fact = None

        if variant and layout != "chart":
            variant = ""
        if variant == "heatmap" and len(str(fact or "").split(",")) < 2:
            variant = ""
        if variant == "donut":
            one = next((f for f in facts if f.id == str(fact).split(",")[0]), None)
            if one is None or not (3 <= len(one.points) <= 5):
                variant = ""

        if layout in ("image_full", "split_image") and image not in pic_ids:
            continue

        bullets = [str(b).strip() for b in (raw.get("bullets") or []) if str(b).strip()][:3]
        slides.append(
            Slide(
                layout=layout,
                title=title,
                fact=fact,
                image=image if image in pic_ids else None,
                note=str(raw.get("note") or "").strip(),
                bullets=bullets,
                variant=variant,
                script=str(raw.get("script") or "").strip(),
            )
        )

    if not slides:
        raise ValueError("模型没有产出可用的页面")
    if slides[0].layout != "title":
        slides.insert(0, Slide(layout="title", title=deck_title or data.get("deck_title") or "汇报"))

    # A second title page in the middle of a deck is the single loudest tell
    # that a machine laid it out. There is exactly one cover and exactly one
    # closing; anything else that claims to be a cover becomes a claim page.
    for i, s in enumerate(slides):
        if i == 0:
            continue
        if s.layout == "title":
            s.layout = "closing" if i == len(slides) - 1 else "bullets"

    # Two section dividers is structure; five is padding.
    seen = 0
    for s in slides:
        if s.layout == "section":
            seen += 1
            if seen > 2:
                s.layout = "bullets"

    sub = str(data.get("subtitle") or "").strip()
    if _echo(sub, ask):
        sub = ""
    return Plan(
        deck_title=deck_title or str(data.get("deck_title") or "汇报").strip(),
        subtitle=sub,
        slides=slides,
    )

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
from dataclasses import dataclass, field

from .compute import Fact
from .ingest import Bundle, Doc, Picture
from .llm import Client

LAYOUTS = {"title", "key_figure", "chart", "bullets", "image_full", "split_image", "closing"}

SYSTEM = """你是一位给中国公司做汇报的资深顾问。你写的不是"PPT"，是一份让人在 90 秒内做决定的材料。

硬性规则，违反任何一条即为不合格：
1. 你绝对不能自己写任何数字。需要数字时，写占位符 {{f3}}（f3 是给定事实的 id）。
   唯一允许你直接写的数字是年份（如 2024）和序号。
2. 每页只讲一个论点。标题是论点，不是名词短语。
   ❌ "销售情况分析"    ✅ "华东拉动全年增长，其余区域基本持平"
3. 要点不超过 3 条，每条不超过 28 个汉字。写不完就拆页，不要缩字号。
4. 不要写"本页介绍了…""综上所述""随着…的发展"这类空话。
5. 叙事顺序固定：先给结论，再给证据，最后给行动。不要目录页。

版式只能从这些里选：
- title       封面，只有大标题和副标题
- key_figure  整页只讲一个数字，配一句解释。用于最重要的那个发现
- chart       图表 + 最多 3 条要点。必须绑定一个 series/breakdown 事实
- bullets     纯要点页，用于没有数据的判断
- image_full  整页一张图，配一句说明。必须绑定一张图
- split_image 左图右文，用于图文结合
- closing     结尾，给行动建议

只输出 JSON，不要任何解释文字。"""

USER_TMPL = """## 用户的原始要求
{ask}

## 我手上有什么
{inventory}

## 事实清单（你只能引用这些 id，不能写数字）
{facts}

## 可用的图片
{pictures}

## 文档摘录
{docs}

## 任务
写一份 {n} 页的汇报。结构：
1. title（封面）
2. key_figure 或 chart —— 最重要的那个发现，结论先行
3. 3–{body} 页证据，每页一个论点，尽量用图表或图片
4. closing（行动建议，2–3 条）

输出这个 JSON：
{{
  "deck_title": "不超过 20 字",
  "subtitle": "不超过 30 字的一句话结论。必须是这份材料自己的判断，不是复述用户的要求",
  "slides": [
    {{"layout": "key_figure", "title": "论点句", "fact": "f3", "note": "一句话解释这个数字意味着什么"}},
    {{"layout": "chart", "title": "论点句", "fact": "f7", "bullets": ["要点一", "要点二"]}},
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
) -> Plan:
    if not facts:
        raise ValueError("没有可引用的事实：先确保输入里有带数值列的表格")
    body = max(2, slides - 3)
    user = USER_TMPL.format(
        ask=ask or "把这份材料整理成一份给管理层看的汇报",
        inventory=_inventory(bundle),
        facts=_facts_block(facts),
        pictures=_pictures_block(bundle.pictures),
        docs=_docs_block(bundle.docs),
        n=slides,
        body=body,
    )
    data = client.json(SYSTEM, user, temperature=0.25, max_tokens=2600)
    return _validate(data, facts, bundle.pictures, deck_title, ask)


def _echo(sub: str, ask: str) -> bool:
    """True when the subtitle is just the user's request read back.

    A model asked for a subtitle will happily paste the ask. That single line
    is the most visible tell that nobody wrote the deck, so it is checked
    rather than trusted.
    """
    if not sub or not ask:
        return False
    import re
    from difflib import SequenceMatcher

    norm = lambda s: re.sub(r"[\s，。、：；！？,.:;!?\"'“”‘’（）()【】\[\]]", "", s)
    a, b = norm(sub), norm(ask)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() > 0.55


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
        if layout in ("chart", "key_figure") and fact not in fact_ids:
            # A chart slide without a valid fact cannot be rendered honestly.
            if layout == "chart":
                continue
            fact = None
        if layout in ("image_full", "split_image") and image not in pic_ids:
            continue
        bullets = [str(b).strip() for b in (raw.get("bullets") or []) if str(b).strip()][:3]
        slides.append(
            Slide(
                layout=layout,
                title=title,
                fact=fact if fact in fact_ids else None,
                image=image if image in pic_ids else None,
                note=str(raw.get("note") or "").strip(),
                bullets=bullets,
            )
        )

    if not slides:
        raise ValueError("模型没有产出可用的页面")
    if slides[0].layout != "title":
        slides.insert(0, Slide(layout="title", title=deck_title or data.get("deck_title") or "汇报"))
    sub = str(data.get("subtitle") or "").strip()
    if _echo(sub, ask):
        sub = ""
    return Plan(
        deck_title=deck_title or str(data.get("deck_title") or "汇报").strip(),
        subtitle=sub,
        slides=slides,
    )

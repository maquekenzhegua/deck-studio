"""One-line edits, written the way a person speaks.

The window has an outline, but editing a title there is still authoring. The
fastest correction path is a sentence: "第三页删掉", "把主题换成午夜",
"第二页标题改成增长只在两个区". This module turns those sentences into plan
mutations -- deterministic, offline, and auditable. Anything it cannot parse
comes back as a question instead of a guess.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .plan import Plan, Slide
from .visual import THEMES

CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
      "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}

ALIASES = {
    "navy": ("navy", "海军", "深蓝", "商务蓝", "默认"),
    "editorial": ("editorial", "编辑", "杂志", "米色", "衬线", "编辑部"),
    "tech": ("tech", "科技", "蓝色", "冷色"),
    "forest": ("forest", "森林", "绿色", "墨绿"),
    "midnight": ("midnight", "午夜", "暗色", "黑色", "深色"),
    # Only registered when a PPTX is in the uploads; harmless otherwise.
    "custom": ("custom", "模板", "我们的配色", "公司配色"),
}


def _page(text: str) -> int | None:
    m = re.search(r"第\s*([0-9]{1,2}|[一二三四五六七八九十]{1,2})\s*页", text)
    if not m:
        return None
    raw = m.group(1)
    if raw.isdigit():
        return int(raw)
    return CN.get(raw)


def theme_for(text: str) -> str | None:
    low = text.lower()
    for tid, words in ALIASES.items():
        for w in words:
            if w in low:
                return tid
    return None


def apply(plan: Plan, text: str) -> tuple[Plan, str]:
    """Return (new plan, what happened). Never mutates the input."""
    raw = (text or "").strip()
    if not raw:
        return plan, "说一句话，比如「第三页删掉」或「换成午夜」。"

    tid = theme_for(raw)
    if tid and ("主题" in raw or "配色" in raw or "风格" in raw or len(raw) <= 6):
        return plan, f"主题换成「{THEMES[tid].label}」，重新排版。"

    idx = _page(raw)
    slides = list(plan.slides)

    if idx is not None and re.search(r"删|去掉|不要", raw):
        if not 1 <= idx <= len(slides):
            return plan, f"没有第 {idx} 页。"
        if len(slides) <= 3:
            return plan, "再删就不成一篇了，先加一页再删。"
        gone = slides.pop(idx - 1)
        return replace(plan, slides=slides), f"删掉第 {idx} 页「{gone.title}」。"

    if idx is not None and re.search(r"上移|提前|往前", raw):
        if not 2 <= idx <= len(slides):
            return plan, f"第 {idx} 页移不上去。"
        slides[idx - 2], slides[idx - 1] = slides[idx - 1], slides[idx - 2]
        return replace(plan, slides=slides), f"第 {idx} 页和上一页换了位置。"

    if idx is not None and re.search(r"下移|延后|往后", raw):
        if not 1 <= idx < len(slides):
            return plan, f"第 {idx} 页移不下去。"
        slides[idx - 1], slides[idx] = slides[idx], slides[idx - 1]
        return replace(plan, slides=slides), f"第 {idx} 页和下一页换了位置。"

    m = re.search(r"第\s*[0-9一二三四五六七八九十]{1,2}\s*页(?:的)?标题"
                  r"(?:改成|换成|写作|写成)[：: ]*(.+)$", raw)
    if m and idx is not None and 1 <= idx <= len(slides):
        title = m.group(1).strip().strip("。")
        old = slides[idx - 1]
        slides[idx - 1] = replace(old, title=title)
        return replace(plan, slides=slides), f"第 {idx} 页标题改成「{title}」。"

    m = re.search(r"(?:总)?标题(?:改成|换成|写作|写成)[：: ]*(.+)$", raw)
    if m:
        title = m.group(1).strip().strip("。")
        return replace(plan, deck_title=title), f"总标题改成「{title}」。"

    m = re.search(r"副标题(?:改成|换成|写作|写成)[：: ]*(.+)$", raw)
    if m:
        sub = m.group(1).strip().strip("。")
        return replace(plan, subtitle=sub), f"副标题改成「{sub}」。"

    if re.search(r"加一页|新增一页|插一页", raw):
        slide = Slide(layout="bullets", title="补一页：这里放什么？", bullets=[])
        return replace(plan, slides=slides + [slide]), "在末尾加了一页空白页。"

    return plan, "没听懂这句。能用的说法：第 N 页删掉 / 第 N 页标题改成… / 换成午夜。"


_theme = theme_for  # older call sites

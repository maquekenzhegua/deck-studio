"""Content-level review of a plan, before anyone renders it.

`verify.py` answers "is every number traceable". `tools/qa.py` answers "did the
file come out readable". Neither answers the question a person asks while
looking at the outline: *is this deck saying anything, in a sensible order?*

This module is that third voice. It reads only the plan and the facts, so it
costs nothing, runs in microseconds, and can gate the build button. Every
finding is a sentence a human could act on, with the page it belongs to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .compute import Fact
from .plan import Plan, Slide

# A title is a claim. Past roughly two lines at 27pt it stops being one.
TITLE_MAX = 26
BULLET_MAX = 4
NUMBER_ONLY = re.compile(r"^[\s\d.,%+\-—–]*$")
PLACEHOLDER = re.compile(r"\{\{\w+\}\}")


@dataclass
class Finding:
    level: str          # "block" | "warn" | "note"
    page: int           # 1-based; 0 means the deck as a whole
    text: str

    def as_dict(self) -> dict:
        return {"level": self.level, "page": self.page, "text": self.text}


def _title_problem(s: Slide, page: int) -> Finding | None:
    title = (s.title or "").strip()
    if not title:
        return Finding("block", page, "这一页没有标题，读者不知道要看的结论是什么。")
    plain = PLACEHOLDER.sub("", title).strip(" ，。·:：")
    if NUMBER_ONLY.match(plain) or not plain:
        return Finding("block", page, "标题只有一个数字或占位符，读起来不是一句判断。")
    # A pull-quote and an excerpt page use the title slot for body text on
    # purpose; measuring them against the headline budget is a false alarm.
    if s.layout in ("quote", "notes"):
        return None
    if len(title) > TITLE_MAX:
        return Finding("warn", page, f"标题 {len(title)} 字，超过 {TITLE_MAX} 字，投影时会挤成三行。")
    return None


def _layout_runs(slides: list[Slide]) -> list[Finding]:
    out: list[Finding] = []
    run_start = 0
    for i in range(1, len(slides) + 1):
        if i == len(slides) or slides[i].layout != slides[run_start].layout:
            run = i - run_start
            if run >= 3 and slides[run_start].layout not in ("bullets", "notes"):
                out.append(Finding(
                    "warn", run_start + 1,
                    f"连续 {run} 页都是「{slides[run_start].layout}」，节奏会平；中间换一页别的版式。"))
            run_start = i
    return out


def _fact_slides(slides: list[Slide], facts: list[Fact]) -> list[Finding]:
    ids = {f.id for f in facts}
    out: list[Finding] = []
    for i, s in enumerate(slides, 1):
        if s.layout in ("chart", "table", "key_figure", "kpi_row"):
            have = [p for p in (s.fact or "").replace(" ", "").split(",") if p]
            if not have:
                out.append(Finding("block", i, "这一页要画图，却没有绑定任何事实。"))
            elif not any(p in ids for p in have):
                out.append(Finding("block", i, "这一页引用的事实 id 都不存在。"))
    return out


def _fact_reuse(slides: list[Slide]) -> list[Finding]:
    """One fact can carry a page; it cannot carry the deck.

    A model that binds region sales to a KPI, a bar chart and a screenshot of
    the same chart has written three pages and said one thing. Two is the
    limit: a headline number may legitimately return in the close.
    """
    seen: dict[str, list[int]] = {}
    for i, s in enumerate(slides, 1):
        for fid in [p for p in (s.fact or "").replace(" ", "").split(",") if p]:
            seen.setdefault(fid, []).append(i)
    out: list[Finding] = []
    for fid, pages in seen.items():
        if len(pages) > 2:
            out.append(Finding(
                "warn", pages[2],
                f"{fid} 已经出现在第 {'、'.join(map(str, pages[:2]))} 页，"
                f"这一页又用了同一条事实；换一条证据，或把两页并成一页。"))
    return out


def _bullets(slides: list[Slide]) -> list[Finding]:
    out: list[Finding] = []
    for i, s in enumerate(slides, 1):
        n = len(s.bullets or [])
        if n > BULLET_MAX and s.layout in ("bullets", "closing"):
            out.append(Finding("warn", i, f"{n} 条要点超过 {BULLET_MAX} 条，观众只会记住最后一条。"))
    return out


def _shape(slides: list[Slide]) -> list[Finding]:
    out: list[Finding] = []
    if not slides:
        return [Finding("block", 0, "没有任何页面。")]
    if slides[0].layout != "title":
        out.append(Finding("warn", 1, "第一页不是封面。"))
    if slides[-1].layout != "closing":
        out.append(Finding("note", len(slides), "最后一页不是收尾页，汇报会结束得很突然。"))
    kinds = {s.layout for s in slides}
    if len(slides) >= 8 and "section" not in kinds:
        out.append(Finding("note", 0, "八页以上没有章节页，长 deck 需要一个停顿点。"))
    if len(slides) > 18:
        out.append(Finding("warn", 0, f"{len(slides)} 页偏长，挑掉不影响论证的页面。"))
    return out


def _duplicate_titles(slides: list[Slide]) -> list[Finding]:
    seen: dict[str, int] = {}
    out: list[Finding] = []
    for i, s in enumerate(slides, 1):
        key = (s.title or "").strip()
        if not key:
            continue
        if key in seen:
            out.append(Finding("warn", i, f"标题与第 {seen[key]} 页完全相同。"))
        else:
            seen[key] = i
    return out


def review(plan: Plan, facts: list[Fact]) -> list[Finding]:
    """Every finding, in page order, cheapest rule first."""
    slides = list(plan.slides or [])
    out: list[Finding] = []
    for i, s in enumerate(slides, 1):
        bad = _title_problem(s, i)
        if bad:
            out.append(bad)
    out += _fact_slides(slides, facts)
    out += _duplicate_titles(slides)
    out += _fact_reuse(slides)
    out += _layout_runs(slides)
    out += _bullets(slides)
    out += _shape(slides)
    out.sort(key=lambda f: (f.page, {"block": 0, "warn": 1, "note": 2}[f.level]))
    return out


def summary(findings: list[Finding]) -> str:
    if not findings:
        return "没有发现问题"
    blocks = sum(1 for f in findings if f.level == "block")
    warns = sum(1 for f in findings if f.level == "warn")
    notes = len(findings) - blocks - warns
    parts = []
    if blocks:
        parts.append(f"{blocks} 处必须改")
    if warns:
        parts.append(f"{warns} 处建议改")
    if notes:
        parts.append(f"{notes} 条提醒")
    return " · ".join(parts)


def as_prompt(findings: list[Finding], limit: int = 6) -> str:
    """The findings, phrased as an instruction a model can act on."""
    lines = ["按这些评审意见改大纲，保留数字可追溯："]
    for i, f in enumerate(findings[:limit], 1):
        where = f"第 {f.page} 页" if f.page else "整份"
        lines.append(f"{i}. {where}：{f.text}")
    return "\n".join(lines)

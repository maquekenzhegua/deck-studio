"""Verify: prove the deck is not lying.

The failure mode this exists to catch is specific and common: a model writes
"同比增长 23%" because 23% sounds like the kind of number a growth slide has.
Nobody notices until the CFO asks where it came from.

So after planning, every figure on every slide is traced back to a computed
fact. Anything untraceable is an error with a slide number, not a warning.

Three more checks earn their keep, because they are the difference between
"correct" and "reads like a person made it":

* a headline that ends in 分析/情况/概述 is a label, not a claim;
* three slides in a row with the same layout is a template, not a deck;
* text that needs more lines than the box has.

Overflow matters because python-pptx will happily render text outside its box
and you only find out when the deck is projected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .compute import Fact
from .plan import Plan, is_noun_phrase
from .render import subst
from .text import estimate_lines
from .theme import CONTENT_W, FS_BODY, FS_TITLE

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
UNIT_AFTER = re.compile(r"^\s*(万|亿|%|倍|个百分点)")
YEAR = re.compile(r"^(19|20)\d{2}$")

# Quantifiers are where a deck lies without any number being wrong. "华北是唯一
# 下滑的大区" passed this module while -4.0% and -12.0% sat in the same chart:
# both numbers traced to a fact, and the word 唯一 was never checked. Narrow on
# purpose -- it only speaks when a slide binds exactly one fact with points.
EXCLUSIVE = re.compile(r"唯一|仅有|只有.{0,6}(一|1)\s*(个|家|项)")
ALL_OF = re.compile(r"全部|所有|均|都|无一例外")
DOWN = re.compile(r"下滑|下降|负增长|减少|萎缩|走低")
UP = re.compile(r"增长|上涨|上升|提升|走高")

# Layouts that carry a figure and therefore must bind a fact.
NEEDS_FACT = ("chart", "key_figure", "table", "kpi_row")
NEEDS_IMAGE = ("image_full", "split_image")


@dataclass
class Problem:
    slide: int
    kind: str      # 'number' | 'overflow' | 'structure' | 'style'
    detail: str

    def __str__(self) -> str:
        return f"第 {self.slide} 页 [{self.kind}] {self.detail}"


def _ids(slide) -> list[str]:
    return [p.strip() for p in (slide.fact or "").split(",") if p.strip()]


def _allowed(facts: list[Fact]) -> set[str]:
    ok: set[str] = set()
    for f in facts:
        for m in NUM.finditer(f.value):
            ok.add(m.group(0).replace(",", ""))
        if f.change is not None:
            ok.add(f"{abs(f.change) * 100:.1f}")
            ok.add(f"{abs(f.change) * 100:.0f}")
        for _, v in f.points:
            for m in NUM.finditer(f"{v:g}"):
                ok.add(m.group(0).replace(",", ""))
            if f.unit == "ratio":
                ok.add(f"{abs(v) * 100:.1f}")
                ok.add(f"{abs(v) * 100:.0f}")
        # A share of the same breakdown is derived, not invented: a page may
        # say "华东占 37%" as long as 37 really is 977/total of these points.
        if len(f.points) > 1:
            total = sum(abs(v) for _, v in f.points)
            if total:
                for _, v in f.points:
                    share = abs(v) / total * 100
                    ok.add(f"{share:.1f}")
                    ok.add(f"{share:.0f}")
        # Step conversion is the whole point of a funnel: 640/1800 = 36% is
        # computed from two printed counts, not invented, so a sentence may
        # quote it as long as the counts are on the page.
        if f.chart == "funnel" and len(f.points) > 1:
            for (_, prev), (_, cur) in zip(f.points, f.points[1:]):
                if prev:
                    step = abs(cur) / abs(prev) * 100
                    ok.add(f"{step:.1f}")
                    ok.add(f"{step:.0f}")
    return ok


def _quantifier_problems(slide, f: Fact | None, idx: int) -> list[Problem]:
    """A superlative on a slide with several data points must be earned."""
    if f is None or len(f.points) < 2:
        return []
    title = slide.title or ""
    vals = [v for _, v in f.points]
    negs = [v for v in vals if v < 0]
    poss = [v for v in vals if v > 0]
    out: list[Problem] = []
    if EXCLUSIVE.search(title):
        if DOWN.search(title) and len(negs) > 1:
            out.append(Problem(idx, "number",
                               f"标题说「唯一」下滑，但同一组数据里有 {len(negs)} 项为负"
                               f"（{', '.join(f'{v:g}' for v in negs)}）"))
        if UP.search(title) and len(poss) > 1 and not DOWN.search(title):
            out.append(Problem(idx, "number",
                               f"标题说「唯一」增长，但同一组数据里有 {len(poss)} 项为正"))
    if ALL_OF.search(title):
        if DOWN.search(title) and negs and poss:
            out.append(Problem(idx, "number",
                               f"标题说全部下滑，但 {len(poss)} 项仍在增长"))
        if UP.search(title) and negs:
            out.append(Problem(idx, "number",
                               f"标题说全部增长，但 {len(negs)} 项在下滑"))
    return out


def check(plan: Plan, facts: list[Fact]) -> list[Problem]:
    fmap = {f.id: f for f in facts}
    allowed = _allowed(facts)
    problems: list[Problem] = []

    run_layout, run_len = "", 0

    for i, s in enumerate(plan.slides, 1):
        texts: list[tuple[str, str]] = [("标题", subst(s.title, fmap))]
        if s.note:
            texts.append(("说明", subst(s.note, fmap)))
        for b in s.bullets:
            texts.append(("要点", subst(b, fmap)))

        for where, text in texts:
            for m in NUM.finditer(text):
                token = m.group(0)
                bare = token.replace(",", "")
                if bare in allowed or YEAR.match(bare):
                    continue
                tail = text[m.end(): m.end() + 4]
                significant = len(bare.replace(".", "")) >= 2 or UNIT_AFTER.match(tail)
                if significant:
                    problems.append(
                        Problem(i, "number", f"{where}里的「{token}」找不到对应事实（原文：{text[:40]}）")
                    )

        ids = _ids(s)
        # Quantifier check: runs on the single fact the page is about.
        if len(ids) == 1:
            problems.extend(_quantifier_problems(s, fmap.get(ids[0]), i))
        if s.layout in NEEDS_FACT and not ids:
            problems.append(Problem(i, "structure", f"{s.layout} 没有绑定事实"))
        if s.layout in NEEDS_FACT:
            missing = [p for p in ids if p not in fmap]
            if missing:
                problems.append(Problem(i, "structure", f"引用了不存在的事实 {','.join(missing)}"))
        if s.layout == "kpi_row" and len(ids) < 2:
            problems.append(Problem(i, "structure", "kpi_row 至少需要 2 个事实"))
        if s.layout in NEEDS_IMAGE and not s.image:
            problems.append(Problem(i, "structure", f"{s.layout} 没有绑定图片"))
        if len(s.bullets) > 3:
            problems.append(Problem(i, "structure", f"要点 {len(s.bullets)} 条，超过 3 条"))

        # A noun-phrase headline is the clearest sign that the model had
        # nothing to say about this page.
        if s.layout not in ("title", "closing", "section") and is_noun_phrase(s.title):
            problems.append(
                Problem(i, "style", f"标题是名词短语不是判断句：「{s.title}」——换成一句结论")
            )

        # Three identical layouts in a row means the deck was assembled by a
        # rule rather than written — except a photo essay, where repeating the
        # same frame is the form.
        if s.layout in ("image_full", "split_image"):
            run_layout, run_len = "", 0
        elif s.layout == run_layout:
            run_len += 1
            if run_len >= 3:
                problems.append(Problem(i, "style", f"连续 {run_len} 页都是 {s.layout}，版式要有变化"))
        else:
            run_layout, run_len = s.layout, 1

        lines = estimate_lines(s.title, CONTENT_W, FS_TITLE)
        if lines > 2:
            problems.append(Problem(i, "overflow", f"标题需要 {lines} 行，最多 2 行"))
        for b in s.bullets:
            n = estimate_lines(b, CONTENT_W, FS_BODY)
            if n > 2:
                problems.append(Problem(i, "overflow", f"要点需要 {n} 行：{b[:24]}"))
    return problems


def report(problems: list[Problem]) -> str:
    if not problems:
        return "✓ 所有数字都能追溯到计算结果，无溢出"
    return "\n".join(str(p) for p in problems)

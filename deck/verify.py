"""Verify: prove the deck is not lying.

The failure mode this exists to catch is specific and common: a model writes
"同比增长 23%" because 23% sounds like the kind of number a growth slide has.
Nobody notices until the CFO asks where it came from.

So after planning, every figure on every slide is traced back to a computed
fact. Anything untraceable is an error with a slide number, not a warning.

Overflow is checked too — python-pptx will happily render text outside its box
and you only find out when the deck is projected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pptx.util import Inches, Pt

from .compute import Fact
from .plan import Plan
from .render import subst
from .text import estimate_lines
from .theme import CONTENT_W

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
UNIT_AFTER = re.compile(r"^\s*(万|亿|%|倍|个百分点)")
YEAR = re.compile(r"^(19|20)\d{2}$")


@dataclass
class Problem:
    slide: int
    kind: str      # 'number' | 'overflow' | 'structure'
    detail: str

    def __str__(self) -> str:
        return f"第 {self.slide} 页 [{self.kind}] {self.detail}"


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
    return ok


def check(plan: Plan, facts: list[Fact]) -> list[Problem]:
    allowed = _allowed(facts)
    problems: list[Problem] = []

    for i, s in enumerate(plan.slides, 1):
        texts: list[tuple[str, str]] = [("标题", subst(s.title, {f.id: f for f in facts}))]
        if s.note:
            texts.append(("说明", subst(s.note, {})))
        for b in s.bullets:
            texts.append(("要点", subst(b, {})))

        for where, text in texts:
            for m in NUM.finditer(text):
                token = m.group(0)
                bare = token.replace(",", "")
                if bare in allowed or YEAR.match(bare):
                    continue
                tail = text[m.end(): m.end() + 4]
                significant = len(bare.replace(".", "")) >= 2 or UNIT_AFTER.match(tail)
                if significant:
                    problems.append(Problem(i, "number", f"{where}里的「{token}」找不到对应事实（原文：{text[:40]}）"))

        if s.layout in ("chart", "key_figure") and not s.fact:
            problems.append(Problem(i, "structure", f"{s.layout} 没有绑定事实"))
        if s.layout in ("image_full", "split_image") and not s.image:
            problems.append(Problem(i, "structure", f"{s.layout} 没有绑定图片"))
        if len(s.bullets) > 3:
            problems.append(Problem(i, "structure", f"要点 {len(s.bullets)} 条，超过 3 条"))

        # Overflow: a title may take two lines, a bullet may take two.
        lines = estimate_lines(s.title, CONTENT_W, Pt(26))
        if lines > 2:
            problems.append(Problem(i, "overflow", f"标题需要 {lines} 行，最多 2 行"))
        for b in s.bullets:
            n = estimate_lines(b, CONTENT_W, Pt(17))
            if n > 2:
                problems.append(Problem(i, "overflow", f"要点需要 {n} 行：{b[:24]}"))
    return problems


def report(problems: list[Problem]) -> str:
    if not problems:
        return "✓ 所有数字都能追溯到计算结果，无溢出"
    return "\n".join(str(p) for p in problems)

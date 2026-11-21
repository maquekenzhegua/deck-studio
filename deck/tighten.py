"""Deterministic tightening for model-written outlines.

A model writes a claim, not a headline: "华东领涨，华北、东北失血，效率卡在方案"
lands at 24 characters, but the next one runs to 31 and wraps to three lines,
which pushes the chart down and costs the page its air. Round-tripping the
model for a trim costs a minute and can rewrite the argument; cutting at the
last punctuation inside the budget keeps the first clause — which is the
claim — and drops only the tail that restates it.

Bullets are treated the same way: a fifth and sixth bullet on one page is not
a slide any more, it is a memo.
"""

from __future__ import annotations

from dataclasses import replace

from .plan import Plan, Slide

LIMIT = 26
BULLETS = 4
CUTS = "，。；、：,.:;"
MIN_HEAD = 8


def trim(title: str) -> str:
    """The longest prefix of `title` that ends at punctuation inside LIMIT."""
    text = (title or "").strip()
    if len(text) <= LIMIT or not text:
        return text
    head = text[:LIMIT]
    cut = -1
    for mark in CUTS:
        cut = max(cut, head.rfind(mark))
    if cut >= MIN_HEAD:
        kept = text[:cut].rstrip()
        if len(kept) >= MIN_HEAD:
            return kept
    # No clean break: a truncated claim is worse than a long one.
    return text


def apply(plan: Plan) -> tuple[Plan, list[str]]:
    log: list[str] = []
    slides: list[Slide] = []
    for i, slide in enumerate(plan.slides, 1):
        title = trim(slide.title)
        if title != slide.title:
            log.append(f"第 {i} 页标题从 {len(slide.title)} 字收到 {len(title)} 字")
        bullets = list(slide.bullets or [])
        if len(bullets) > BULLETS:
            log.append(f"第 {i} 页要点从 {len(bullets)} 条收到 {BULLETS} 条")
            bullets = bullets[:BULLETS]
        slides.append(replace(slide, title=title, bullets=bullets))
    return replace(plan, slides=slides), log


def fit_pages(plan: Plan, want: int) -> tuple[Plan, list[str]]:
    """Cut a deck down to `want` pages without losing its skeleton.

    The title, every section mark and the closing page are structure, not
    content: dropping them to hit a number would leave a deck that no longer
    reads as a deck. Everything between is sampled evenly so the argument keeps
    its shape instead of keeping only the opening.
    """
    total = len(plan.slides)
    if want <= 0 or total <= want:
        return plan, []
    fixed = {0} | {i for i, s in enumerate(plan.slides) if s.layout == "section"}
    fixed.add(total - 1)
    if len(fixed) >= want:
        # Not enough room even for the skeleton: keep the ends and the first
        # section mark, drop the rest — better a short deck than a broken one.
        keep = sorted({0, total - 1} | (sorted(fixed - {0, total - 1})[: max(0, want - 2)]))
    else:
        room = want - len(fixed)
        pool = [i for i in range(total) if i not in fixed]
        step = len(pool) / room if room else 1
        picked = [pool[min(len(pool) - 1, int(i * step))] for i in range(room)]
        keep = sorted(fixed | set(picked))
    slides = [plan.slides[i] for i in keep]
    return replace(plan, slides=slides), [f"按「{want} 页」删掉 {total - len(slides)} 页，保留章节与收尾"]

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

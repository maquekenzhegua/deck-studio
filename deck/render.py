"""Render: plan + facts -> .pptx

Nothing here decides *what* to say. It decides where things sit, using only the
constants in `theme.py`. That separation is what stops the deck from looking
generated: the model's job ends at the sentence, layout is code.
"""

from __future__ import annotations

import re
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu, Inches, Pt

from . import charts, fmt
from .compute import Fact
from .ingest import Picture
from .plan import Plan, Slide
from .text import (
    add_bullets, add_numbered_list, add_rect, add_rule, add_text,
    estimate_height, estimate_lines,
)
from .theme import (
    ACCENT, ACCENT_SOFT, CONTENT_H, CONTENT_W, FS_BODY, FS_CAPTION, FS_FOOT,
    FS_SECTION, FS_TITLE, HAIRLINE, INK, INK_2, MARGIN_BOTTOM, MARGIN_TOP,
    MARGIN_X, MUTED, PAPER, SLIDE_H, SLIDE_W, col, colx,
)

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

# Vertical rhythm shared by every content slide.
TITLE_TOP = MARGIN_TOP
TITLE_H = Inches(1.05)
RULE_TOP = Inches(1.82)
BODY_TOP = Inches(2.12)
BODY_H = Inches(4.30)
FOOT_TOP = SLIDE_H - MARGIN_BOTTOM - Inches(0.12)


def subst(text: str, facts: dict[str, Fact]) -> str:
    """Replace {{f3}} with the computed display string."""
    def repl(m: re.Match[str]) -> str:
        f = facts.get(m.group(1))
        return f.value if f else m.group(0)
    return PLACEHOLDER.sub(repl, text or "")


def _fit(text: str, width: Emu, max_pt: int, min_pt: int) -> Pt:
    """Largest size at which a single line still fits."""
    size = max_pt
    while size > min_pt:
        if len(text) * size * 0.56 * 12700 <= float(width):
            return Pt(size)
        size -= 2
    return Pt(min_pt)


def _footer(slide, idx: int, deck_title: str) -> None:
    add_text(slide, MARGIN_X, FOOT_TOP, col(8), Inches(0.22), deck_title,
             size=FS_FOOT, color=MUTED)
    add_text(slide, colx(10), FOOT_TOP, col(2), Inches(0.22), f"{idx:02d}",
             size=FS_FOOT, color=MUTED, align="right")


def _header(slide, title: str, *, size=FS_SECTION) -> None:
    lines = estimate_lines(title, CONTENT_W, size)
    h = Emu(int(lines * size.pt * 1.18 * 12700))
    add_text(slide, MARGIN_X, TITLE_TOP, CONTENT_W, h, title, size=size, color=INK, bold=True,
             line_spacing=1.12)
    add_rule(slide, MARGIN_X, RULE_TOP, Inches(0.72), color=ACCENT)


def _place_picture(slide, path: str, left: Emu, top: Emu, width: Emu, height: Emu):
    """Contain-fit, centred in the box. Never distorts."""
    from PIL import Image

    with Image.open(path) as im:
        iw, ih = im.size
    if not iw or not ih:
        return None
    scale = min(float(width) / iw, float(height) / ih)
    w, h = int(iw * scale), int(ih * scale)
    x = int(left + (float(width) - w) / 2)
    y = int(top + (float(height) - h) / 2)
    return slide.shapes.add_picture(path, Emu(x), Emu(y), Emu(w), Emu(h))


# --- layouts --------------------------------------------------------------

def _title_slide(prs, plan: Plan, slide: Slide) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    add_rect(s, Inches(0), Inches(0), Inches(0.16), SLIDE_H, fill=ACCENT)
    add_text(s, Inches(1.15), Inches(2.28), Inches(10.6), Inches(1.4),
             subst(slide.title, {}), size=FS_TITLE, color=INK, bold=True, line_spacing=1.06)
    add_rule(s, Inches(1.15), Inches(3.86), Inches(0.9), color=ACCENT)
    sub = subst(plan.subtitle, {})
    if not sub:
        # No subtitle is better than a restatement of the request. Fall back to
        # the deck's own first claim, which is what a reader wants on a cover.
        for cand in plan.slides[1:]:
            if cand.layout in ("key_figure", "chart") and cand.title:
                sub = subst(cand.title, {})
                break
    if sub:
        add_text(s, Inches(1.15), Inches(4.10), Inches(9.6), Inches(0.9),
                 sub, size=FS_BODY, color=INK_2, line_spacing=1.35)


def _key_figure(prs, plan: Plan, slide: Slide, facts: dict[str, Fact], idx: int) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    f = facts[slide.fact] if slide.fact else None
    _header(s, subst(slide.title, facts))
    if f is None:
        return
    # Eyebrow first, then the number. Printing "华东 34.0%" all at 104pt makes
    # the category compete with the value; the category is context.
    eyebrow = f"{f.column} · {f.top}" if f.top else f.label
    add_text(s, MARGIN_X, Inches(2.20), col(8), Inches(0.3),
             eyebrow, size=FS_CAPTION, color=MUTED)
    size = _fit(f.number or f.value, col(7), 108, 44)
    add_text(s, MARGIN_X, Inches(2.58), col(7), Inches(1.60), f.number or f.value,
             size=size, color=ACCENT, bold=True, line_spacing=1.0)
    # The commentary sits beside the number rather than under it: a single
    # huge figure with a column of prose to its right reads as an editorial
    # page, and it uses the canvas instead of hugging the left margin.
    note = subst(slide.note, facts)
    if note:
        add_rect(s, colx(7.6), Inches(2.66), Emu(9525), Inches(1.35), fill=HAIRLINE)
        add_text(s, colx(8.2), Inches(2.60), col(3.6), Inches(1.6), note,
                 size=Pt(18), color=INK, line_spacing=1.5)
    add_text(s, MARGIN_X, Inches(5.72), col(9), Inches(0.3),
             f"来源：{f.source.split(' · ')[0]}", size=FS_CAPTION, color=MUTED)
    _footer(s, idx, plan.deck_title)


def _stack_h(items: list[str], width: Emu, size, gap: int) -> float:
    """Height of an add_bullets stack, in EMU."""
    from .text import estimate_lines

    if not items:
        return 0.0
    total = 0.0
    for i, item in enumerate(items):
        lines = estimate_lines(item, width - Pt(20), size)
        total += lines * size.pt * 1.24 * 12700
        if i:
            total += gap * 12700
    return total


def _chart(prs, plan: Plan, slide: Slide, facts: dict[str, Fact], idx: int) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    f = facts[slide.fact] if slide.fact else None
    _header(s, subst(slide.title, facts))
    if f is None:
        return
    items = [subst(b, facts) for b in slide.bullets]
    has_side = bool(items)
    w = col(7) if has_side else CONTENT_W
    h = BODY_H if not has_side else Inches(4.15)
    img = charts.render(f, f"{f.id}-{idx}", w=7.2 if has_side else 10.4, h=h.inches)
    if img:
        _place_picture(s, img, MARGIN_X, BODY_TOP, w, h)
    if has_side:
        # A rule between chart and commentary, and the commentary optically
        # centred against the plot instead of hanging off the top edge.
        add_rect(s, colx(7.35), BODY_TOP, Emu(9525), Inches(3.85), fill=HAIRLINE)
        x = colx(8)
        tw = col(4)
        block = _stack_h(items, tw, FS_BODY, 10)
        top = Emu(int(BODY_TOP + max(0.0, (float(h) - block) / 2)))
        add_bullets(s, x, top, tw, Emu(int(block) + 12700), items, size=FS_BODY)
    _footer(s, idx, plan.deck_title)


def _bullets(prs, plan: Plan, slide: Slide, facts: dict[str, Fact], idx: int) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _header(s, subst(slide.title, facts))
    items = [subst(b, facts) for b in slide.bullets]
    if not items:
        items = ["（模型没有给出要点）"]
    if len(items) <= 4:
        # Short claim list: give each claim a row of its own.
        add_numbered_list(s, MARGIN_X, BODY_TOP + Inches(0.15), CONTENT_W,
                          Inches(4.05), items, size=Pt(24))
    else:
        add_bullets(s, MARGIN_X, BODY_TOP + Inches(0.2), col(9), Inches(3.8),
                    items, size=Pt(20), gap=20)
    _footer(s, idx, plan.deck_title)


def _image_full(prs, plan: Plan, slide: Slide, pics: dict[str, Picture], idx: int) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _header(s, subst(slide.title, {}))
    p = pics.get(slide.image or "")
    if p:
        _place_picture(s, p.path, MARGIN_X, BODY_TOP, CONTENT_W, Inches(3.55))
    note = subst(slide.note, {})
    if note:
        add_text(s, MARGIN_X, Inches(6.05), CONTENT_W, Inches(0.4), note,
                 size=FS_CAPTION, color=MUTED)
    _footer(s, idx, plan.deck_title)


def _split_image(prs, plan: Plan, slide: Slide, facts: dict[str, Fact],
                 pics: dict[str, Picture], idx: int) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _header(s, subst(slide.title, facts))
    p = pics.get(slide.image or "")
    pic_h = Inches(3.55)
    if p:
        _place_picture(s, p.path, MARGIN_X, BODY_TOP, col(6), pic_h)
        # Name the file the figure came from. A chart with no provenance is
        # the first thing a reviewer questions.
        add_text(s, MARGIN_X, Emu(int(BODY_TOP + pic_h) + 40000), col(6), Inches(0.24),
                 f"图：{p.name}", size=FS_FOOT, color=MUTED)
    items = [subst(b, facts) for b in slide.bullets]
    note = subst(slide.note, facts)
    x = colx(7)
    tw = col(5)
    # Centre the commentary against the figure instead of hanging it off the
    # top edge, which leaves the lower half of the slide empty.
    block = 0.0
    if note:
        block += float(estimate_height(note, tw, Pt(19), 1.45))
    if items:
        block += float(_stack_h(items, tw, FS_BODY, 12)) + 12700 * 18
    y = Emu(int(BODY_TOP + max(0.0, (float(pic_h) - block) / 2)))
    if note:
        add_text(s, x, y, tw, Inches(1.2), note, size=Pt(19), color=INK, line_spacing=1.45)
        y = Emu(int(y + estimate_height(note, tw, Pt(19), 1.45) + 12700 * 18))
    if items:
        add_bullets(s, x, y, tw, Emu(int(_stack_h(items, tw, FS_BODY, 12)) + 12700),
                    items, size=FS_BODY, gap=12)
    _footer(s, idx, plan.deck_title)


def _closing(prs, plan: Plan, slide: Slide, facts: dict[str, Fact], idx: int) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    add_rect(s, Inches(0), Inches(0), Inches(0.16), SLIDE_H, fill=ACCENT)
    add_text(s, Inches(1.15), Inches(1.30), Inches(10.4), Inches(0.9),
             subst(slide.title, facts), size=Pt(34), color=INK, bold=True)
    add_rule(s, Inches(1.15), Inches(2.30), Inches(0.9), color=ACCENT)
    items = [subst(b, facts) for b in slide.bullets]
    if items:
        # Same row treatment as a claim slide: the last page is the one people
        # photograph, so it gets structure rather than a second bullet dump.
        add_numbered_list(s, Inches(1.15), Inches(2.78), Inches(10.9),
                          Inches(3.55), items[:4], size=Pt(24))
    _footer(s, idx, plan.deck_title)


def build(plan: Plan, facts: list[Fact], pictures: list[Picture], out: str) -> str:
    fmap = {f.id: f for f in facts}
    pmap = {p.id: p for p in pictures}
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    for i, slide in enumerate(plan.slides, 1):
        if slide.layout == "title":
            _title_slide(prs, plan, slide)
        elif slide.layout == "key_figure":
            _key_figure(prs, plan, slide, fmap, i)
        elif slide.layout == "chart":
            _chart(prs, plan, slide, fmap, i)
        elif slide.layout == "bullets":
            _bullets(prs, plan, slide, fmap, i)
        elif slide.layout == "image_full":
            _image_full(prs, plan, slide, pmap, i)
        elif slide.layout == "split_image":
            _split_image(prs, plan, slide, fmap, pmap, i)
        elif slide.layout == "closing":
            _closing(prs, plan, slide, fmap, i)

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out

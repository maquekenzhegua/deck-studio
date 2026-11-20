"""Render: plan + facts -> .pptx

Nothing here decides *what* to say. It decides where things sit, using only the
constants in `theme.py`. That separation is what stops the deck from looking
generated: the model's job ends at the sentence, layout is code.

The visual grammar is the one consulting decks actually use, because that is
what "看起来像公司做的" means in practice:

* A full-bleed navy cover carrying the deck's single most important number.
  Not a title floating in white space with a coloured stripe beside it.
* A headline at 26pt that states a claim, a hairline under it, and a page
  number in the corner. No boxes around anything.
* Content laid on a 12-column grid so slides line up with each other when
  flipped through, which is the thing the eye reads as "designed".
* One accent colour used for exactly one purpose. Everything else is ink,
  secondary ink, or a hairline.
"""

from __future__ import annotations

import re
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu, Inches, Pt

from . import charts, compute
from .compute import Fact
from .ingest import Picture
from .plan import Plan, Slide
from .text import (
    add_bullets, add_numbered_list, add_rect, add_rule, add_text,
    estimate_height, estimate_lines,
)
from .theme import (
    ALERT, BODY_H, BODY_TOP, CONTENT_W, DATA_BAR, FS_BODY, FS_CAPTION,
    FS_COVER, FS_FOOT, FS_HERO,
    GUTTER,
    FS_KPI, FS_KPI_MD, FS_LEAD, FS_SECTION, FS_TITLE, HAIRLINE, INK, INK_2, MARGIN_BOTTOM,
    MARGIN_TOP, MARGIN_X, MUTED, PAPER, PRIMARY, PRIMARY_MID, PRIMARY_SOFT,
    RULE_TOP, SLIDE_H, SLIDE_W, TITLE_H, TITLE_TOP, WHITE, col, colx,
)

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
FOOT_TOP = SLIDE_H - MARGIN_BOTTOM - Inches(0.10)


# --- helpers --------------------------------------------------------------

def tidy(text: str) -> str:
    """Collapse the stutters a language model leaves behind.

    "华东同比华东 34.0%" is the commonest one: the subject gets restated after
    the metric. In a finished deck that reads as a typo, and a deterministic
    fix is cheaper and safer than another planning round.
    """
    if not text:
        return text
    out = re.sub(r"([\u4e00-\u9fa5]{2,4})(同比|环比)\1", r"\1\2", text)
    out = re.sub(r"([\u4e00-\u9fa5])\1{2,}", r"\1\1", out)
    out = re.sub(r"[，,]{2,}", "，", out)
    return out.strip()


def subst(text: str, facts: dict[str, Fact]) -> str:
    """Replace {{f3}} with the computed display string."""
    def repl(m: re.Match[str]) -> str:
        f = facts.get(m.group(1))
        return f.value if f else m.group(0)
    return tidy(PLACEHOLDER.sub(repl, text or ""))


def _ids(slide: Slide) -> list[str]:
    """A slide may bind one fact or several, comma-separated."""
    raw = (slide.fact or "").replace(" ", "")
    return [p for p in raw.split(",") if p]


def _fit(text: str, width: Emu, max_pt: int, min_pt: int) -> Pt:
    """Largest size at which a single line still fits."""
    size = max_pt
    while size > min_pt:
        if len(text) * size * 0.56 * 12700 <= float(width):
            return Pt(size)
        size -= 2
    return Pt(min_pt)


def _footer(slide, idx: int, deck_title: str) -> None:
    """Page number only. The deck title used to sit here too, one line above
    the source note, and the two together read as a double footer."""
    add_text(slide, colx(11), FOOT_TOP, col(1), Inches(0.20), f"{idx:02d}",
             size=FS_FOOT, color=MUTED, align="right")


def _header(slide, title: str) -> None:
    """Headline + hairline. The rule is what makes a stack of pages read as
    one document instead of a stack of pages."""
    lines = max(1, min(2, estimate_lines(title, col(10), FS_TITLE)))
    h = Emu(int(lines * FS_TITLE.pt * 1.16 * 12700))
    add_text(slide, MARGIN_X, TITLE_TOP, col(10), h, title,
             size=FS_TITLE, color=INK, bold=True, line_spacing=1.10)
    add_rect(slide, MARGIN_X, RULE_TOP, CONTENT_W, Emu(9525), fill=HAIRLINE)


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


def _stack_h(items: list[str], width: Emu, size, gap: int) -> float:
    """Height of an add_bullets stack, in EMU."""
    if not items:
        return 0.0
    total = 0.0
    for i, item in enumerate(items):
        lines = estimate_lines(item, width - Pt(20), size)
        total += lines * size.pt * 1.24 * 12700
        if i:
            total += gap * 12700
    return total


def _bg(slide, color) -> None:
    """Full-bleed background. A shape, not a slide-fill, so it also works when
    the deck is later merged into a template that owns the master."""
    add_rect(slide, Inches(0), Inches(0), SLIDE_W, SLIDE_H, fill=color)


# --- layouts --------------------------------------------------------------

def _title_slide(prs, plan: Plan, slide: Slide, facts: dict[str, Fact]) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(s, PRIMARY)

    eyebrow = plan.deck_title.strip()
    if not eyebrow or eyebrow == slide.title.strip():
        eyebrow = "经营汇报"
    add_text(s, MARGIN_X, Inches(1.36), col(11), Inches(0.30),
             eyebrow, size=FS_CAPTION, color=PRIMARY_SOFT)

    title = subst(slide.title, facts)
    lines = max(1, min(3, estimate_lines(title, col(11), FS_COVER)))
    title_h = Emu(int(lines * FS_COVER.pt * 1.16 * 12700))
    add_text(s, MARGIN_X, Inches(1.78), col(11), title_h,
             title, size=FS_COVER, color=WHITE, bold=True, line_spacing=1.10)

    # The subtitle belongs directly under the headline. Pushing it to a fixed
    # coordinate leaves a hole the size of a paragraph between them, which is
    # what makes a cover read as unfinished rather than airy.
    sub = subst(plan.subtitle, facts)
    sub_top = Emu(int(Inches(1.78) + title_h) + 12700 * 26)
    if sub:
        add_text(s, MARGIN_X, sub_top, col(9), Inches(0.86),
                 sub, size=FS_LEAD, color=PRIMARY_SOFT, line_spacing=1.45)

    # The cover carries the deck's single most important number. A cover with
    # only words wastes the one page everybody looks at.
    # The cover carries the number the deck is actually arguing about, so it is
    # chosen by what kind of number it is, not by which page comes first. A
    # count of orders is a fact; a growth rate is the argument. Falls back to a
    # money figure, then to whatever the first data page uses.
    used = [facts[i] for sid in plan.slides for i in _ids(sid) if i in facts]
    hero = next((f for f in used if f.number and f.unit == "ratio"), None) \
        or next((f for f in used if f.number and f.unit == "money"), None) \
        or next((f for f in used if f.number), None)
    if hero:
        add_rect(s, MARGIN_X, Inches(5.58), Inches(0.9), Pt(3), fill=PRIMARY_MID)
        add_text(s, MARGIN_X, Inches(5.80), col(6), Inches(0.72),
                 hero.number, size=Pt(50), color=WHITE, bold=True, line_spacing=1.0)
        add_text(s, MARGIN_X, Inches(6.56), col(8), Inches(0.28),
                 f"{hero.column} · {hero.top}" if hero.top else hero.label,
                 size=FS_CAPTION, color=PRIMARY_SOFT)


def _section(prs, plan: Plan, slide: Slide, facts: dict[str, Fact], idx: int) -> None:
    """A divider. Used at most twice; more than that is padding."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(s, PRIMARY_SOFT)
    add_text(s, MARGIN_X, Inches(2.62), col(11), Inches(0.32),
             f"{idx:02d}", size=FS_SECTION, color=PRIMARY)
    add_text(s, MARGIN_X, Inches(3.02), col(10), Inches(0.86),
             subst(slide.title, facts), size=FS_COVER, color=PRIMARY, bold=True)
    note = subst(slide.note, facts)
    if note:
        add_text(s, MARGIN_X, Inches(4.06), col(9), Inches(0.6),
                 note, size=FS_LEAD, color=INK_2)


def _key_figure(prs, plan: Plan, slide: Slide, facts: dict[str, Fact], idx: int) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _header(s, subst(slide.title, facts))
    ids = _ids(slide)
    f = facts.get(ids[0]) if ids else None
    if f is None:
        _footer(s, idx, plan.deck_title)
        return

    eyebrow = f"{f.column} · {f.top}" if f.top else f.label
    num = f.number or f.value
    num_size = _fit(num, col(6), 128, 64)
    note = subst(slide.note, facts)
    src = f"来源：{f.source.split(' · ')[0]}"

    # Lay the block out as one stack and centre it in the body band. Placing
    # each element at a fixed coordinate leaves the lower half of the page
    # empty, which reads as "there was nothing else to say".
    num_h = num_size.pt * 1.15 * 12700
    # num_h is already EMU; multiplying it by 12700 again made `stack`
    # astronomically large, so the block was always pinned to BODY_TOP.
    stack = (FS_SECTION.pt * 1.4 + num_size.pt * 1.15 + FS_CAPTION.pt * 2.4) * 12700
    top = Emu(int(BODY_TOP + max(0.0, (float(BODY_H) - stack) / 2)))

    add_text(s, MARGIN_X, top, col(6), Inches(0.26), eyebrow, size=FS_SECTION, color=MUTED)
    num_top = Emu(int(top) + int(FS_SECTION.pt * 1.4 * 12700))
    add_text(s, MARGIN_X, num_top, col(6), Emu(int(num_h)), num,
             size=num_size, color=PRIMARY, bold=True, line_spacing=1.0,
             anchor="middle")
    src_top = Emu(int(num_top) + int(num_h) + 12700 * 16)
    add_text(s, MARGIN_X, src_top, col(6), Inches(0.24), src,
             size=FS_CAPTION, color=MUTED)

    if note:
        # The rule spans exactly the commentary, so the two read as one object.
        note_h = estimate_height(note, col(4), Pt(19), 1.55)
        rule_h = max(note_h, Inches(1.0))
        add_rect(s, colx(7), Emu(int(num_top) - int(Inches(0.10))), Emu(9525), rule_h,
                 fill=HAIRLINE)
        add_text(s, colx(8), Emu(int(num_top) - int(Inches(0.06))), col(4), Emu(int(note_h)),
                 note, size=Pt(19), color=INK, line_spacing=1.55)
    _footer(s, idx, plan.deck_title)


def _kpi_row(prs, plan: Plan, slide: Slide, facts: dict[str, Fact], idx: int) -> None:
    """Up to four figures side by side, separated by hairlines, sharing a top
    edge and a baseline. Cards with boxes and shadows are the template look;
    rules between aligned numbers are the report look.

    Geometry rule, learned from rendering it wrong three times: content runs
    continuously from the header down, and leftover space goes at the bottom of
    the page. Centring the figures in the band, or pinning the closing sentence
    to the page bottom, both put the hole in the *middle* -- which reads as a
    layout failure rather than as breathing room.

    The caption under each number is suppressed when it would repeat the label
    above it -- "订单数 / 4,685 / 订单数" says the same thing twice and reads
    like a form field nobody filled in.
    """
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _header(s, subst(slide.title, facts))
    fs = [facts[i] for i in _ids(slide) if i in facts][:4]
    if not fs:
        _footer(s, idx, plan.deck_title)
        return

    note = subst(slide.note, facts)
    n = len(fs)
    span = int(12 / n)

    # Pass one: size every figure, so the block height is known before drawing.
    laid = []
    for i, f in enumerate(fs):
        x = colx(i * span)
        w = col(span - 1) if i < n - 1 else col(span)
        eyebrow = f.column if not f.top else f"{f.column} · {f.top}"
        num = f.number or f.value
        pt = _fit(num, w, FS_KPI_MD.pt, 30)
        has_label = bool(f.label.strip()) and f.label.strip() != eyebrow
        laid.append((x, w, eyebrow, num, pt, has_label, f))

    num_h = max(int(t[4].pt * 1.20 * 12700) for t in laid)
    label_h = int(Inches(0.36))
    block_h = num_h + (label_h if any(t[5] for t in laid) else 0)
    block_top = Emu(int(BODY_TOP) + int(Inches(0.24)))

    for i, (x, w, eyebrow, num, pt, has_label, f) in enumerate(laid):
        if i:
            add_rect(s, Emu(int(x) - int(Inches(0.18))),
                     Emu(int(block_top) - int(Inches(0.06))),
                     Emu(9525), Emu(block_h + int(Inches(0.12))), fill=HAIRLINE)
        add_text(s, x, block_top, w, Inches(0.30), eyebrow,
                 size=FS_CAPTION, color=MUTED)
        ny = Emu(int(block_top) + int(Inches(0.34)))
        add_text(s, x, ny, w, Emu(num_h), num, size=pt, color=PRIMARY,
                 bold=True, line_spacing=1.0, anchor="middle")
        if has_label:
            add_text(s, x, Emu(int(ny) + int(num_h)), w, Emu(label_h),
                     f.label, size=FS_CAPTION, color=INK_2, line_spacing=1.3)

    # Pass two: the takeaway goes in a filled band, not as a loose sentence.
    # Three figures and one line cannot fill a 5in band by being spread out --
    # spreading leaves a hole in the middle, which is what made this page read
    # as unfinished. A conclusion band is a real element: it gives the page a
    # bottom edge, carries the interpretation at reading size, and stops the
    # eye before the empty space below it.
    if note:
        note_pt = FS_LEAD
        text_w = Emu(int(CONTENT_W) - int(Inches(0.90)))
        note_h = estimate_height(note, text_w, note_pt, 1.45)
        band_h = Emu(int(note_h) + int(Inches(0.72)))
        band_y = Emu(int(block_top) + int(Inches(0.34)) + int(block_h)
                     + int(Inches(0.52)))
        add_rect(s, MARGIN_X, band_y, CONTENT_W, band_h, fill=PRIMARY_SOFT)
        add_rect(s, MARGIN_X, band_y, Emu(int(Inches(0.05))), band_h, fill=PRIMARY)
        add_text(s, Emu(int(MARGIN_X) + int(Inches(0.45))),
                 Emu(int(band_y) + int(Inches(0.36))), text_w, Emu(int(note_h)),
                 note, size=note_pt, color=INK, line_spacing=1.45)

    add_text(s, MARGIN_X, FOOT_TOP - Inches(0.30), col(10), Inches(0.22),
             f"来源：{fs[0].source.split(' · ')[0]}", size=FS_FOOT, color=MUTED)
    _footer(s, idx, plan.deck_title)


def _chart(prs, plan: Plan, slide: Slide, facts: dict[str, Fact], idx: int) -> None:
    """One chart, one takeaway.

    When the takeaway is a single sentence it goes under the chart at full
    width instead of into a narrow right column: a 4-column text rail next to a
    7-column chart leaves both halves looking unfinished, and the sentence gets
    hyphen-wrapped into six short lines. Short takeaway -> full-width chart.
    """
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _header(s, subst(slide.title, facts))
    ids = _ids(slide)
    f = facts.get(ids[0]) if ids else None
    if f is None:
        _footer(s, idx, plan.deck_title)
        return

    items = [subst(b, facts) for b in slide.bullets]
    # A rail is for bullets that need to be read as a column. Three eight-word
    # labels in a rail leave both halves of the page half-empty; they belong in
    # one takeaway line under a full-width chart.
    rail = len(items) >= 2 and sum(len(x) for x in items) >= 50
    if rail:
        w = col(8)
        h = BODY_H - Inches(0.30)
    else:
        w = CONTENT_W
        h = BODY_H - (Inches(0.86) if items else Inches(0.30))

    img = charts.render(f, f"{f.id}-{idx}", w=float(w) / 914400, h=float(h) / 914400)
    if img:
        _place_picture(s, img, MARGIN_X, BODY_TOP + Inches(0.04), w, h)

    if rail:
        add_rect(s, colx(8), BODY_TOP + Inches(0.10), Emu(9525),
                 Emu(int(h) - int(Inches(0.20))), fill=HAIRLINE)
        x, tw = colx(9), col(3)
        block = _stack_h(items, tw, FS_BODY, 14)
        top = Emu(int(BODY_TOP + max(0.0, (float(h) - block) / 2)))
        add_bullets(s, x, top, tw, Emu(int(block) + 12700), items, size=FS_BODY, gap=14)
    elif items:
        y = Emu(int(BODY_TOP) + int(h) + int(Inches(0.30)))
        add_rect(s, MARGIN_X, y, Inches(0.34), Emu(19050), fill=PRIMARY)
        add_text(s, Emu(int(MARGIN_X) + int(Inches(0.52))), Emu(int(y) - int(Inches(0.10))),
                 Emu(int(CONTENT_W) - int(Inches(0.52))), Inches(0.52),
                 " · ".join(items[:3]), size=FS_LEAD, color=INK, line_spacing=1.3)

    add_text(s, MARGIN_X, FOOT_TOP - Inches(0.24), col(10), Inches(0.22),
             f"来源：{f.source.split(' · ')[0]}", size=FS_FOOT, color=MUTED)
    _footer(s, idx, plan.deck_title)


def _table(prs, plan: Plan, slide: Slide, facts: dict[str, Fact], idx: int) -> None:
    """A ranking table drawn from rules and text, not a pptx table object.

    The pptx table object inherits a theme that is almost always wrong, and
    every cell needs its margins reset by hand. Rules + text boxes are fewer
    lines and look the way a report's table looks.

    The third column is a proportion only when the fact is a composition. For
    a funnel or a ranking, "share of the total" is not a real quantity — the
    stages are not parts of one whole — so it is replaced by a bar, which
    says the same thing without asserting something false.
    """
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _header(s, subst(slide.title, facts))
    ids = _ids(slide)
    f = facts.get(ids[0]) if ids else None
    if f is None or not f.points:
        _footer(s, idx, plan.deck_title)
        return

    # A funnel is a strictly shrinking run of positive counts, and the number
    # that matters in a funnel is the step conversion -- the absolute count at
    # each stage says nothing on its own. Detected here rather than declared by
    # the model, because "this is a funnel" is a property of the data.
    funnel = f.chart == "funnel"
    rows = sorted(f.points, key=lambda kv: -abs(kv[1]))[:7]
    share = f.chart == "pie"
    total = sum(abs(v) for _, v in f.points) or 1
    peak = max(abs(v) for _, v in f.points) or 1

    # Columns are spans between grid lines, not sums of column widths. Adding
    # widths leaves out the gutters, so every boundary drifts 0.2in further
    # off-grid than the one before it -- which is what "snap 55%" was.
    x0 = MARGIN_X
    x_name, x_val, x_bar = colx(1), colx(6), colx(9)
    w_num = Emu(int(x_name) - int(x0))
    w_name = Emu(int(x_val) - int(x_name))
    w_val = Emu(int(x_bar) - int(x_val))
    w_bar = Emu(int(colx(12)) - int(x_bar))

    head_h = Inches(0.34)
    avail = float(BODY_H) - float(head_h) - float(Inches(0.16))
    row_h = min(Inches(1.05), Emu(int(avail / len(rows))))
    block_h = int(row_h) * len(rows)
    top = Emu(int(BODY_TOP + max(0.0, (avail - block_h) / 2)))

    last_head = "转化" if funnel else ("占比" if share else "")
    # Header alignment has to follow the cells under it, or "数值" and "转化"
    # run together into one word.
    last_align = "right" if (funnel or share) else "left"
    for label, x, w, align in (("项目", x_name, w_name, "left"),
                               ("数值", x_val, w_val, "right"),
                               (last_head, x_bar, w_bar, last_align)):
        if label:
            add_text(s, x, top, w, Inches(0.26), label, size=FS_CAPTION, color=MUTED, align=align)
    add_rect(s, x0, Emu(int(top) + int(Inches(0.30))), CONTENT_W, Emu(9525), fill=PRIMARY)

    # Only the worst step gets the alert colour. Colouring every step below
    # 50% turns three numbers red and points at nothing.
    steps = {nm: r for nm, _, r in compute.funnel_steps(f.points)} if funnel else {}
    worst = min(steps.values()) if steps else 0.0

    fmt_v = charts._labeler(f.unit)
    for i, (name, val) in enumerate(rows):
        y = Emu(int(top) + int(Inches(0.36)) + int(row_h) * i)
        add_text(s, x0, Emu(int(y) + int(row_h * 0.26)), w_num, Inches(0.28),
                 f"{i + 1:02d}", size=FS_CAPTION, color=MUTED)
        add_text(s, x_name, Emu(int(y) + int(row_h * 0.18)), w_name, Inches(0.32),
                 name, size=FS_BODY, color=INK)
        add_text(s, x_val, Emu(int(y) + int(row_h * 0.18)), w_val, Inches(0.32),
                 fmt_v(val), size=FS_BODY, color=INK, align="right", bold=True)
        if funnel:
            # Row i shows what share of stage i-1 arrived. The first row has
            # nothing to convert from, so it stays blank rather than printing a
            # meaningless 100%.
            order = [nm for nm, _ in f.points]
            k = order.index(name)
            if k:
                before = f.points[k - 1][1]
                ratio = val / before if before else 0.0
                # Below half is the step that is actually costing money, so it
                # is the one cell allowed to carry the alert colour.
                add_text(s, x_bar, Emu(int(y) + int(row_h * 0.18)), w_bar, Inches(0.32),
                         f"{ratio * 100:.1f}%", size=FS_BODY,
                         color=ALERT if ratio <= worst else INK_2, align="right")
        elif share:
            add_text(s, x_bar, Emu(int(y) + int(row_h * 0.18)), w_bar, Inches(0.32),
                     f"{abs(val) / total * 100:.1f}%", size=FS_BODY, color=INK_2, align="right")
        else:
            # A gap between the number and its bar: touching them makes the
            # bar look like a highlight on the text rather than a quantity.
            gap = Inches(0.16)
            room = int(float(w_bar) - float(gap))
            bw = Emu(int(room * abs(val) / peak))
            add_rect(s, Emu(int(x_bar) + int(gap)), Emu(int(y) + int(row_h * 0.40)),
                     bw, Pt(11), fill=DATA_BAR)
        if i < len(rows) - 1:
            add_rect(s, x0, Emu(int(y) + int(row_h)), CONTENT_W, Emu(9525), fill=HAIRLINE)

    add_text(s, MARGIN_X, FOOT_TOP - Inches(0.24), col(10), Inches(0.22),
             f"来源：{f.source.split(' · ')[0]}", size=FS_FOOT, color=MUTED)
    _footer(s, idx, plan.deck_title)


def _bullets(prs, plan: Plan, slide: Slide, facts: dict[str, Fact], idx: int) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _header(s, subst(slide.title, facts))
    items = [subst(b, facts) for b in slide.bullets]
    if not items:
        items = ["（模型没有给出要点）"]
    lead = subst(slide.note, facts)

    body_top = BODY_TOP + Inches(0.10)
    y = body_top
    if lead:
        lead_h = estimate_height(lead, col(9), FS_LEAD, 1.4)
        add_text(s, MARGIN_X, y, col(9), Emu(int(lead_h)), lead,
                 size=FS_LEAD, color=INK_2, line_spacing=1.4)
        y = Emu(int(y) + int(lead_h) + 12700 * 30)

    if len(items) <= 4:
        # Cap the row height so three claims do not stretch to fill the page;
        # a third of a page per sentence reads as a form, not a slide.
        #
        # No vertical centring. Centring leaves a hole under the title and a
        # second hole at the bottom, and the corpus is unambiguous about where
        # content starts: upper-margin p90 is 20% of the canvas, so real decks
        # hang the body off the header rather than floating it mid-page.
        room = float(BODY_TOP + BODY_H) - float(y)
        row = min(Inches(1.30), Emu(int(room / len(items))))
        block = Emu(int(row) * len(items))
        add_numbered_list(s, MARGIN_X, y, CONTENT_W, block, items,
                          size=FS_LEAD, max_row=row)
    else:
        add_bullets(s, MARGIN_X, y, col(9), Emu(int(BODY_H) - int(Inches(0.4))),
                    items, size=Pt(17), gap=18)
    _footer(s, idx, plan.deck_title)


def _image_full(prs, plan: Plan, slide: Slide, facts: dict[str, Fact],
                pics: dict[str, Picture], idx: int) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _header(s, subst(slide.title, facts))
    p = pics.get(slide.image or "")
    if p:
        _place_picture(s, p.path, MARGIN_X, BODY_TOP + Inches(0.08), CONTENT_W, Inches(3.72))
        add_text(s, MARGIN_X, Inches(5.62), CONTENT_W, Inches(0.24),
                 f"图：{p.name}", size=FS_FOOT, color=MUTED)
    note = subst(slide.note, facts)
    if note:
        add_text(s, MARGIN_X, Inches(5.94), col(10), Inches(0.30), note,
                 size=FS_CAPTION, color=INK_2)
    _footer(s, idx, plan.deck_title)


def _split_image(prs, plan: Plan, slide: Slide, facts: dict[str, Fact],
                 pics: dict[str, Picture], idx: int) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _header(s, subst(slide.title, facts))
    p = pics.get(slide.image or "")
    pic_h = BODY_H - Inches(0.42)
    if p:
        _place_picture(s, p.path, MARGIN_X, BODY_TOP + Inches(0.06), col(6), pic_h)
        add_text(s, MARGIN_X, Emu(int(BODY_TOP + pic_h) + 50000), col(6), Inches(0.22),
                 f"图：{p.name}", size=FS_FOOT, color=MUTED)
    items = [subst(b, facts) for b in slide.bullets]
    note = subst(slide.note, facts)
    x = colx(7)
    tw = col(5)
    block = 0.0
    if note:
        block += float(estimate_height(note, tw, Pt(18), 1.45))
    if items:
        block += float(_stack_h(items, tw, FS_BODY, 14)) + 12700 * 22
    y = Emu(int(BODY_TOP + max(0.0, (float(pic_h) - block) / 2)))
    # Same divider the chart page uses, so a reader turning pages sees one
    # system rather than two layouts that happen to be adjacent.
    add_rect(s, colx(6) + GUTTER, Emu(int(BODY_TOP) + int(Inches(0.10))), Emu(9525),
             Emu(int(pic_h) - int(Inches(0.20))), fill=HAIRLINE)
    if note:
        note_h = estimate_height(note, tw, Pt(18), 1.45)
        add_text(s, x, y, tw, Emu(int(note_h)), note, size=Pt(18), color=INK, line_spacing=1.45)
        y = Emu(int(y + note_h + 12700 * 22))
    if items:
        add_bullets(s, x, y, tw, Emu(int(_stack_h(items, tw, FS_BODY, 14)) + 12700),
                    items, size=FS_BODY, gap=14)
    _footer(s, idx, plan.deck_title)


def _closing(prs, plan: Plan, slide: Slide, facts: dict[str, Fact]) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(s, PRIMARY)
    add_text(s, MARGIN_X, Inches(1.06), col(10), Inches(0.30),
             "下一步", size=FS_SECTION, color=PRIMARY_SOFT)
    add_text(s, MARGIN_X, Inches(1.46), col(10), Inches(0.80),
             subst(slide.title, facts), size=FS_COVER, color=WHITE, bold=True)

    items = [subst(b, facts) for b in slide.bullets][:4]
    y = Inches(2.72)
    row_h = Inches(0.92)
    for i, item in enumerate(items, 1):
        yy = Emu(int(y) + int(row_h) * (i - 1))
        add_rect(s, MARGIN_X, yy, col(11), Emu(9525), fill=PRIMARY_MID)
        add_text(s, MARGIN_X, Emu(int(yy) + int(Inches(0.26))), col(1), Inches(0.34),
                 f"{i:02d}", size=FS_SECTION, color=PRIMARY_SOFT, bold=True)
        add_text(s, colx(1), Emu(int(yy) + int(Inches(0.22))), col(10), Inches(0.48),
                 item, size=FS_LEAD, color=WHITE, line_spacing=1.35)


# --- entry ----------------------------------------------------------------

def build(plan: Plan, facts: list[Fact], pictures: list[Picture], out: str) -> str:
    fmap = {f.id: f for f in facts}
    pmap = {p.id: p for p in pictures}
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    for i, slide in enumerate(plan.slides, 1):
        layout = slide.layout
        if layout == "title":
            _title_slide(prs, plan, slide, fmap)
        elif layout == "section":
            _section(prs, plan, slide, fmap, i)
        elif layout == "key_figure":
            _key_figure(prs, plan, slide, fmap, i)
        elif layout == "kpi_row":
            _kpi_row(prs, plan, slide, fmap, i)
        elif layout == "chart":
            _chart(prs, plan, slide, fmap, i)
        elif layout == "table":
            _table(prs, plan, slide, fmap, i)
        elif layout == "bullets":
            _bullets(prs, plan, slide, fmap, i)
        elif layout == "image_full":
            _image_full(prs, plan, slide, fmap, pmap, i)
        elif layout == "split_image":
            _split_image(prs, plan, slide, fmap, pmap, i)
        elif layout == "closing":
            _closing(prs, plan, slide, fmap)

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out

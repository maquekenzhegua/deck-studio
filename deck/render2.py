"""Render v2: the deck as an art-directed object, not a form.

v1 laid every page out correctly and every page looked the same: title,
hairline, content, 22% ink, white space at the bottom. Correct is not the
same as designed. Real decks — the 19 investor decks in `corpus/refs` —
work on four things v1 did not have:

1. **A field, not a page.** Covers and dividers are a full-bleed colour or
   photograph. Content pages are paper. Alternating them is what gives a
   deck rhythm when someone flips through it.
2. **Scale contrast.** A 96pt number next to 11pt labels, or a 40pt claim on
   a dark field. v1 kept everything between 11 and 38pt, which reads as a
   document, not a presentation.
3. **One argument per page, drawn.** The number is not in a table cell; it
   is the page. The chart is not a figure in a corner; it is the left two
   thirds with a reading rail beside it.
4. **Photography as a material.** Cropped, duotoned to the deck's palette,
   bled to the edge. Screenshots and charts are treated as documents —
   framed on paper — because a chart blown up full-bleed looks like a
   mistake.

This module owns geometry only. Words still come from `plan.py`, numbers
still come from `compute.py`, and `verify.py` still refuses a slide whose
figures cannot be traced.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Inches, Pt

from . import charts2, fmt
from .compute import Fact
from .ingest import Picture
from .plan import Plan, Slide
from .render import _ids, subst
from .visual import (
    CONTENT_W, FOOT_TOP, GUTTER, MARGIN_X, SLIDE_H, SLIDE_W,
    Theme, add_gradient, add_line, add_picture, add_rect, add_scrim, add_text,
    block_height, col, colx, content_path, estimate_lines, footer_rule, get_theme,
    image_is_flat, kicker, page_number, source_note, text_width_pt,
)

PX = 914400
BODY_TOP = Inches(1.72)
RAIL_X = Inches(9.30)
RAIL_W = Inches(3.40)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _rgb(hex_value: str) -> RGBColor:
    h = hex_value.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _mix(a: str, b: str, t: float) -> RGBColor:
    """Blend two hex colours. Used for ghost type and quiet rules."""
    ca, cb = _rgb(a), _rgb(b)
    return RGBColor(
        int(ca[0] + (cb[0] - ca[0]) * t),
        int(ca[1] + (cb[1] - ca[1]) * t),
        int(ca[2] + (cb[2] - ca[2]) * t),
    )


def _bg(slide, theme: Theme, dark: bool = False) -> None:
    add_rect(slide, 0, 0, SLIDE_W, SLIDE_H,
             _rgb(theme.dark if dark else theme.paper))


def _num_fit(text: str, width, max_pt: float, min_pt: float) -> Pt:
    """A figure that has to stay on one line.

    `_fit` estimates width, and the estimate is a little optimistic for digits
    plus a CJK unit: "2,502.1万" passed at 78pt and then the renderer broke the
    line between the number and 万, dropping 万 onto the label. Standalone
    figures are rendered with wrap off, so the only honest fix is to shrink
    until the string actually fits.
    """
    size = Pt(max_pt)
    limit = float(width) / 914400 * 72 * 0.98
    while size.pt > min_pt and text_width_pt(text, size.pt) * 1.07 > limit:
        size = Pt(size.pt - 1)
    return size


def _fit(text: str, width, height, max_pt: int, min_pt: int,
         line_spacing: float = 1.18) -> Pt:
    """Largest size at which the whole paragraph still fits the box."""
    size = max_pt
    while size > min_pt:
        if block_height(text, width, size, line_spacing) <= float(height):
            break
        size -= 1
    return Pt(size)


def _head(slide, s: Slide, theme: Theme, facts: dict[str, Fact], *,
          on_dark: bool = False, top: float = 0.30) -> None:
    """Kicker, headline, rule. The shared spine of every content page."""
    label = "经营复盘" if on_dark else "经营复盘"
    kicker(slide, label, theme, top=Inches(top), on_dark=on_dark)
    title = subst(s.title, facts)
    top_pt = theme.head_pt or 27.0
    size = _fit(title, Inches(11.3), Inches(0.92), top_pt, top_pt - 7, 1.08)
    title = _wrap_clauses(title, Inches(11.3), size.pt)
    add_text(slide, MARGIN_X, Inches(top + 0.34), Inches(11.3), Inches(0.94),
             title, theme, size=size, color=_rgb(theme.dark_ink if on_dark else theme.ink),
             bold=True, font="headline", line_spacing=1.08)
    rule = _rgb(theme.dark_muted if on_dark else theme.hair)
    add_rect(slide, MARGIN_X, Inches(1.44), CONTENT_W, Emu(9525), rule)


def _page(slide, idx: int, total: int, theme: Theme, s: Slide, facts: dict[str, Fact],
          *, on_dark: bool = False) -> None:
    footer_rule(slide, theme, on_dark)
    src = subst(s.note if s.note and s.layout == "table" else "", facts)
    source_note(slide, src, theme, on_dark)
    page_number(slide, idx, theme, total, on_dark)


def _unit(f: Fact) -> str:
    return {"money": "元", "count": "个", "ratio": "%"}.get(f.unit or "", "")


def _year(plan: Plan) -> str:
    m = re.search(r"(20\d{2})", f"{plan.deck_title} {plan.subtitle}")
    return m.group(1) if m else str(datetime.now().year)


def _split_lead(text: str) -> tuple[str, str]:
    """'结论：解释' -> ('结论', '解释'). Lets a bullet carry a bold lead-in
    without asking the planner for markup it has no way to produce."""
    parts = re.split(r"[：:]\s*", text, maxsplit=1)
    if len(parts) == 2 and 0 < len(parts[0]) <= 16:
        return parts[0], parts[1]
    return "", text


def _wrap_clauses(text: str, width, size_pt: float) -> str:
    """Break a Chinese headline at punctuation, never inside a word.

    PowerPoint's own wrap is happy to put one character of the next clause on
    the previous line -- "…北方，明 / 年押服务". Every reader sees that as a
    machine error. Greedy packing on clause boundaries produces the line break
    an editor would have chosen.
    """
    text = str(text).strip()
    if not text:
        return text
    limit = float(width) / 12700.0 * 0.995
    parts = re.split(r"(?<=[，。；：、,;:])", text)
    lines: list[str] = []
    cur = ""
    for part in parts:
        cand = cur + part
        if cur and text_width_pt(cand, size_pt) > limit:
            lines.append(cur)
            cur = part.lstrip()
        else:
            cur = cand
    if cur:
        lines.append(cur)
    out: list[str] = []
    for line in lines:
        if text_width_pt(line, size_pt) <= limit:
            out.append(line)
            continue
        buf = ""
        for ch in line:
            if buf and text_width_pt(buf + ch, size_pt) > limit:
                out.append(buf)
                buf = ch
            else:
                buf += ch
        if buf:
            out.append(buf)
    return "\n".join(out)


def _value(v: float, unit: str | None) -> str:
    if unit == "ratio":
        return f"{v * 100:.1f}%"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:.1f}亿"
    if a >= 1e4:
        return f"{v / 1e4:.0f}万"
    if a >= 1000:
        return f"{v:,.0f}"
    return f"{v:g}"


# --------------------------------------------------------------------------
# cover
# --------------------------------------------------------------------------


def cover(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme,
          pics: dict[str, Picture] | None = None) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme, dark=True)
    photo = next((p for p in (pics or {}).values()
                  if p.kind == "photo" and Path(p.path).exists()), None)
    if photo:
        # Real decks open on a photograph, not a gradient: the image carries
        # the subject, a scrim carries the words.
        _photo(slide, photo.path, 0, 0, SLIDE_W, SLIDE_H, theme, duotone=True)
        # Two-part scrim: solid behind the words, fading over the subject.
        # A single 92→46 gradient left white type sitting on a bright sky.
        # Flat behind the words, then one smooth fade. A solid block butted
        # against a two-stop gradient shows a seam where the renderer bands.
        add_scrim(slide, 0, 0, SLIDE_W, SLIDE_H, _rgb(theme.dark),
                  [(0, 100), (34, 100), (72, 26), (100, 30)])
        # The KPI row and footer sit in the lower third: a second scrim from
        # below keeps white numerals off the bright part of the photograph.
        add_scrim(slide, 0, Inches(4.60), SLIDE_W, Inches(2.90), _rgb(theme.dark),
                  [(0, 0), (100, 42)], angle=90)
    else:
        add_gradient(slide, 0, 0, SLIDE_W, SLIDE_H,
                     _rgb(theme.dark), _mix(theme.dark, "#FFFFFF", 0.10), angle=35)
        # A drawn field, not decoration: one ring gives the page a centre of
        # gravity without inventing an illustration.
        ring = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(8.05), Inches(-1.55),
                                      Inches(7.1), Inches(7.1))
        ring.fill.background()
        ring.line.color.rgb = _mix(theme.dark, "#FFFFFF", 0.24)
        ring.line.width = Emu(19050)
        ring.shadow.inherit = False

    add_rect(slide, MARGIN_X, Inches(1.26), Inches(0.62), Emu(38100), _rgb(theme.accent))
    add_text(slide, MARGIN_X, Inches(1.44), Inches(7.0), Inches(0.26),
             "经营复盘  ·  STRATEGY REVIEW", theme, size=Pt(10),
             color=_rgb(theme.dark_muted), bold=True, spacing=2.6)

    title = subst(plan.deck_title, facts)
    size = _fit(title, Inches(8.35), Inches(2.45), 46, 30, 1.06)
    title = _wrap_clauses(title, Inches(8.35), size.pt)
    add_text(slide, MARGIN_X, Inches(1.92), Inches(8.35), Inches(2.55),
             title, theme, size=size, color=_rgb(theme.dark_ink), bold=True,
             font="headline", line_spacing=1.06, spacing=-0.6)

    sub = subst(plan.subtitle, facts)
    if sub:
        add_text(slide, MARGIN_X, Inches(4.38), Inches(7.15), Inches(0.82),
                 sub, theme, size=Pt(16.5), color=_rgb(theme.dark_muted), line_spacing=1.35)

    # The cover earns its keep by carrying the three numbers the deck is
    # about. Taken from the first KPI slide, so they are computed facts and
    # the verifier still owns them.
    kpi_slide = next((x for x in plan.slides if x.layout == "kpi_row"), None)
    stats = [facts[i] for i in (_ids(kpi_slide) if kpi_slide else []) if i in facts][:3]
    if stats:
        width = Inches(3.55)
        for i, f in enumerate(stats):
            x = MARGIN_X + Inches(i * 3.78)
            add_rect(slide, x, Inches(5.35), width, Emu(9525),
                     _mix(theme.dark, "#FFFFFF", 0.30))
            figure = f.number or f.value
            add_text(slide, x, Inches(5.48), width, Inches(0.46),
                     figure, theme, size=_num_fit(figure, width - Inches(0.10), 25, 16),
                     color=_rgb(theme.dark_ink), bold=True, font="headline",
                     spacing=-0.8, wrap=False)
            add_text(slide, x, Inches(5.98), width - Inches(0.30), Inches(0.26),
                     f"{f.label} · {_unit(f)}", theme, size=Pt(9.5),
                     color=_rgb(theme.dark_muted), spacing=0.8)

    add_rect(slide, MARGIN_X, Inches(6.54), CONTENT_W, Emu(6350),
             _mix(theme.dark, "#FFFFFF", 0.22))
    add_text(slide, MARGIN_X, Inches(6.68), Inches(6.0), Inches(0.24),
             f"{len(plan.slides)} 页  ·  {_year(plan)} 年经营复盘", theme, size=Pt(9),
             color=_rgb(theme.dark_muted), spacing=1.4)


# --------------------------------------------------------------------------
# section
# --------------------------------------------------------------------------


def section(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme,
            idx: int, sec: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme, dark=True)
    add_gradient(slide, 0, 0, SLIDE_W, SLIDE_H,
                 _rgb(theme.dark), _mix(theme.dark, theme.accent, 0.22), angle=20)

    num = f"{sec:02d}"
    add_text(slide, Inches(7.1), Inches(1.05), Inches(6.4), Inches(4.2),
             num, theme, size=Pt(300), color=_mix(theme.dark, "#FFFFFF", 0.07),
             bold=True, align="right", font="headline", spacing=-6)

    add_text(slide, MARGIN_X, Inches(2.02), Inches(6.2), Inches(0.26),
             f"SECTION {num}", theme, size=Pt(10.5), color=_rgb(theme.accent),
             bold=True, spacing=3.0)
    add_rect(slide, MARGIN_X, Inches(2.36), Inches(0.86), Emu(38100), _rgb(theme.accent))
    title = subst(s.title, facts)
    size = _fit(title, Inches(7.4), Inches(2.2), 44, 30, 1.14)
    title = _wrap_clauses(title, Inches(7.4), size.pt)
    add_text(slide, MARGIN_X, Inches(2.62), Inches(7.6), Inches(2.3),
             title, theme, size=size, color=_rgb(theme.dark_ink), bold=True,
             font="headline", line_spacing=1.14)
    if s.note:
        # Sit the note below however many lines the title actually took; a
        # fixed y collides the moment the headline wraps to two lines.
        lines = title.count("\n") + 1
        note_y = 2.62 + lines * (size.pt * 1.14 / 72) + 0.46
        add_text(slide, MARGIN_X, Inches(note_y), Inches(6.9), Inches(0.9),
                 subst(s.note, facts), theme, size=Pt(16),
                 color=_rgb(theme.dark_muted), line_spacing=1.4)

    # agenda: where this chapter sits in the whole story
    secs = [x for x in plan.slides if x.layout == "section"]
    if len(secs) == 1:
        # single-chapter deck: preview what this chapter will show
        # Preview the chapter, not the whole deck: stop at the next section.
        upcoming = []
        for i, x in enumerate(plan.slides[idx:], start=idx + 1):
            if x.layout in ("section", "closing") or len(upcoming) >= 5:
                break
            upcoming.append((i, x))
        if upcoming:
            add_rect(slide, MARGIN_X, Inches(5.16), Inches(7.30), Emu(6350),
                     _mix(theme.dark, "#FFFFFF", 0.18))
            for k, (page, item) in enumerate(upcoming):
                yy = 5.32 + k * 0.36
                add_text(slide, MARGIN_X, Inches(yy), Inches(0.55), Inches(0.28),
                         f"{page:02d}", theme, size=Pt(9), bold=True,
                         color=_rgb(theme.dark_muted))
                label = subst(item.title, facts)
                label = label if len(label) <= 26 else label[:25] + "…"
                add_text(slide, MARGIN_X + Inches(0.60), Inches(yy - 0.02),
                         Inches(5.95), Inches(0.32), label, theme, size=Pt(11.5),
                         color=_rgb(theme.dark_ink), line_spacing=1.2)
            page_number(slide, idx, theme)
            return
    if len(secs) > 1:
        add_rect(slide, MARGIN_X, Inches(5.62), Inches(7.30), Emu(6350),
                 _mix(theme.dark, "#FFFFFF", 0.18))
        step = 7.30 / len(secs)
        for i, item in enumerate(secs, start=1):
            cx = MARGIN_X + Inches(step * (i - 1))
            here = i == sec
            add_text(slide, cx, Inches(5.80), Inches(step - 0.12), Inches(0.22),
                     f"{i:02d}", theme, size=Pt(9), bold=True,
                     color=_rgb(theme.accent if here else theme.dark_muted))
            label = subst(item.title, facts)
            label = label if len(label) <= 14 else label[:13] + "…"
            add_text(slide, cx, Inches(6.02), Inches(step - 0.12), Inches(0.5),
                     label, theme, size=Pt(12), bold=here,
                     color=_rgb(theme.dark_ink if here else theme.dark_muted),
                     line_spacing=1.25)
    page_number(slide, idx, theme)


# --------------------------------------------------------------------------
# statement: one number owns the page
# --------------------------------------------------------------------------


def _clean_label(label: str | None) -> str:
    """`销售额 按 区域` reads like a database column; `销售额 · 区域` reads."""
    text = (label or "").strip()
    return re.sub(r"\s*按\s*", " · ", text)


def _point_number(v: float, unit: str | None) -> str:
    if unit == "ratio":
        return f"{v * 100:.1f}%"
    if unit == "money":
        return fmt.money(v).replace(" ", "")
    return fmt.num(v)


def _short(v: float, unit: str | None) -> str:
    if unit == "ratio":
        return f"{v * 100:.1f}%"
    if unit == "money":
        return fmt.money(v)
    return fmt.num(v)


def statement(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme,
              idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme)
    ids = _ids(s)
    f = facts.get(ids[0]) if ids else None

    add_text(slide, MARGIN_X, Inches(0.34), Inches(6.0), Inches(0.26),
             "一个数字", theme, size=Pt(9.5), color=_rgb(theme.accent),
             bold=True, spacing=2.6)
    title = subst(s.title, facts)
    size = _fit(title, Inches(6.15), Inches(2.15), 34, 24, 1.12)
    title = _wrap_clauses(title, Inches(6.05), size.pt)
    add_text(slide, MARGIN_X, Inches(1.02), Inches(6.15), Inches(2.2),
             title, theme, size=size, color=_rgb(theme.ink), bold=True,
             font="headline", line_spacing=1.12)
    add_rect(slide, MARGIN_X, Inches(3.42), Inches(0.72), Emu(38100), _rgb(theme.accent))
    note_end = 3.10
    if s.note:
        note = subst(s.note, facts)
        add_text(slide, MARGIN_X, Inches(3.64), Inches(5.85), Inches(1.95),
                 note, theme, size=Pt(16.5), color=_rgb(theme.ink2), line_spacing=1.45)
        nlines = estimate_lines(note, Inches(5.75), 16.5)
        note_end = 3.64 + nlines * 16.5 * 1.45 / 72.0
    if f:
        src = (f.source or "").split(" / ")[0]
        # Sit the source under the argument instead of pinning it to the foot:
        # a 1.7in hole between note and source reads as a missing element.
        add_text(slide, MARGIN_X, Inches(max(note_end + 0.55, 4.55)),
                 Inches(6.2), Inches(0.24),
                 f"{_clean_label(f.label)}　·　{Path(src).name}", theme, size=Pt(9),
                 color=_rgb(theme.muted), spacing=0.4)

    add_rect(slide, Inches(7.12), Inches(0.92), Inches(5.59), Inches(5.72),
             _rgb(theme.soft))
    add_rect(slide, Inches(7.12), Inches(0.92), Inches(5.59), Emu(38100), _rgb(theme.accent))
    if f:
        number = f.number or f.value
        sub = f.value
        label = f.label
        extra = None
        if len(f.points) > 1:
            # A breakdown fact has no single number; the statement page then
            # argues with its leader instead of printing the whole list at 104pt.
            ranked = sorted(f.points, key=lambda kv: -abs(kv[1]))
            top = ranked[0]
            denom = sum(abs(v) for _, v in f.points) or 1
            number = _point_number(top[1], f.unit)
            label = _clean_label(f.label)
            share = abs(top[1]) / denom
            extra = f"{top[0]}占 {share * 100:.0f}%"
            if len(ranked) > 1 and ranked[1][1]:
                extra += f"，比第二名高 {(abs(top[1]) / abs(ranked[1][1]) - 1) * 100:.0f}%"
            sub = "  ·  ".join(f"{n} {_short(v, f.unit)}" for n, v in ranked[1:4])
        size = _fit(number, Inches(4.65), Inches(1.55), 92, 54, 1.0)
        # A hero number must never wrap: PowerPoint will happily break between
        # "977.2" and "万", which drops a lone 万 onto the label below.
        while size.pt > 54 and text_width_pt(number, size.pt) > 4.72 * 72:
            size = Pt(size.pt - 1)
        # Set the unit smaller and sit it on the baseline: a 92pt 万 next to
        # 977.2 is a type error, the one place where "make it bigger" is wrong.
        m = re.match(r"^(.*?)(万亿|亿|万|元|个|家|单|%|pt)$", number)
        if m and m.group(1):
            digits, unit = m.group(1), m.group(2)
            u_size = Pt(round(size.pt * 0.36))
            # text_width_pt is an estimate; typographic tracking and the
            # font's own sidebearings make real digits 3–6% wider. Without the
            # margin the unit lands on top of the last digit.
            w_digits = text_width_pt(digits, size.pt) * 1.05
            w_unit = text_width_pt(unit, u_size.pt)
            x0 = Inches(7.35) + int((float(Inches(5.15)) - (w_digits + w_unit + 8) * 12700) / 2)
            add_text(slide, Emu(x0), Inches(2.04), Inches(4.2), Inches(1.6),
                     digits, theme, size=size, color=_rgb(theme.accent), bold=True,
                     font="headline", line_spacing=1.0, spacing=-2.0, wrap=False)
            add_text(slide, Emu(x0 + int((w_digits + 8) * 12700)),
                     Inches(2.04 + size.pt * 0.50 / 72.0), Inches(1.2), Inches(0.8),
                     unit, theme, size=u_size, color=_rgb(theme.accent), bold=True,
                     font="headline", line_spacing=1.0, wrap=False)
        else:
            add_text(slide, Inches(7.35), Inches(2.12), Inches(5.15), Inches(1.55),
                     number, theme, size=size, color=_rgb(theme.accent), bold=True,
                     font="headline", align="center", line_spacing=1.0, spacing=-2.0,
                     wrap=False)
        add_text(slide, Inches(7.45), Inches(3.92), Inches(4.95), Inches(0.34),
                 label, theme, size=Pt(12),
                 color=_rgb(theme.ink2), bold=True, align="center", spacing=1.2)
        if extra:
            add_text(slide, Inches(7.45), Inches(4.30), Inches(4.95), Inches(0.30),
                     extra, theme, size=Pt(12), color=_rgb(theme.accent),
                     bold=True, align="center")
        if f.change is not None:
            add_text(slide, Inches(7.45), Inches(4.66 if extra else 4.30),
                     Inches(4.95), Inches(0.30),
                     f"同比 {fmt.signed_pct(f.change)}", theme, size=Pt(12),
                     color=_rgb(theme.negative if f.change < 0 else theme.accent),
                     bold=True, align="center")
        add_text(slide, Inches(7.45), Inches(5.32), Inches(4.95), Inches(0.9),
                 sub, theme, size=Pt(12.5), color=_rgb(theme.muted),
                 align="center", line_spacing=1.3)
    page_number(slide, idx, theme, total)
    footer_rule(slide, theme)


# --------------------------------------------------------------------------
# kpi row
# --------------------------------------------------------------------------


def kpi(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme,
        idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme)
    _head(slide, s, theme, facts)

    ids = _ids(s)
    fs = [facts[i] for i in ids if i in facts][:4]
    if not fs:
        page_number(slide, idx, theme, total)
        return
    n = len(fs)
    span = 12 // n
    width = col(span) if n > 1 else CONTENT_W

    for i, f in enumerate(fs):
        x = colx(i * span)
        rule = _rgb(theme.accent if i == 0 else theme.hair)
        add_rect(slide, x, Inches(1.84), width, Emu(28575), rule)
        add_text(slide, x, Inches(2.02), width - Inches(0.20), Inches(0.24),
                 f"{f.label} · {_unit(f)}", theme, size=Pt(10.5),
                 color=_rgb(theme.muted), bold=True, spacing=1.6)
        number = f.number or f.value
        size = _num_fit(number, width - Inches(0.12), 78, 40)
        add_text(slide, x, Inches(2.34), width, Inches(1.38),
                 number, theme, size=size, color=_rgb(theme.ink), bold=True, wrap=False,
                 font="headline", line_spacing=1.0, spacing=-1.5)
        if f.change is not None:
            add_text(slide, x, Inches(3.74), width, Inches(0.28),
                     f"同比 {fmt.signed_pct(f.change)}", theme, size=Pt(12),
                     color=_rgb(theme.negative if f.change < 0 else theme.accent),
                     bold=True)
        foot = f"最高 {f.top}" if f.top else ""
        if foot:
            add_text(slide, x, Inches(3.98), width, Inches(0.30),
                     foot, theme, size=Pt(10.5), color=_rgb(theme.muted))

    if s.note:
        add_rect(slide, MARGIN_X, Inches(4.72), CONTENT_W, Inches(1.10), _rgb(theme.soft))
        add_rect(slide, MARGIN_X, Inches(4.72), Emu(38100), Inches(1.10), _rgb(theme.accent))
        add_text(slide, Inches(0.92), Inches(4.94), Inches(1.2), Inches(0.24),
                 "读法", theme, size=Pt(9.5), color=_rgb(theme.accent),
                 bold=True, spacing=2.2)
        note = subst(s.note, facts)
        size = _fit(note, Inches(10.5), Inches(0.86), 17, 12, 1.32)
        add_text(slide, Inches(1.72), Inches(4.94), Inches(10.5), Inches(0.86),
                 note, theme, size=size, color=_rgb(theme.ink2), line_spacing=1.3)
        files = dict.fromkeys(
            (f.source or "").split(" / ")[0] for f in fs if f.source)
        names = "、".join(Path(x).name for x in files if x)
        labels = " / ".join(f.label for f in fs)
        if names:
            add_text(slide, MARGIN_X, Inches(6.22), CONTENT_W, Inches(0.24),
                     f"来源 · {names}  ·  {labels}", theme, size=Pt(9),
                     color=_rgb(theme.muted), spacing=0.6)
    page_number(slide, idx, theme, total)
    footer_rule(slide, theme)


# --------------------------------------------------------------------------
# chart with a reading rail
# --------------------------------------------------------------------------


def _fallback_readings(f: Fact) -> list[str]:
    pts = sorted(f.points, key=lambda kv: -kv[1])
    if len(pts) < 2:
        return []
    out = [f"{pts[0][0]} 排在第一，{pts[-1][0]} 排在最后。"]
    neg = [k for k, v in pts if v < 0]
    pos = [k for k, v in pts if v > 0]
    if neg and pos:
        out.append(f"{'、'.join(neg)} 为负，{'、'.join(pos)} 为正。")
    elif neg:
        out.append(f"{'、'.join(neg)} 全部为负。")
    gap = pts[0][1] - pts[-1][1]
    out.append(f"首位与末位相差 {_value(gap, f.unit)}。")
    return out[:3]


def chart(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme,
          idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme)
    _head(slide, s, theme, facts)
    ids = _ids(s)
    f = facts.get(ids[0]) if ids else None

    if f and (f.points or getattr(f, "series", None)):
        png = None
        if s.variant == "heatmap" and len(ids) > 1:
            png = _heat_png(ids, facts, f"{idx}", theme)
        elif s.variant == "donut" and f.points:
            png = charts2.donut(f.points, f"{idx}", theme,
                                w=8.05, h=4.42, fmt=charts2._labeler(f.unit), unit=f.unit)
        else:
            png = charts2.render(f, f"{idx}", theme, w=8.05, h=4.42)
        if png:
            slide.shapes.add_picture(png, MARGIN_X, Inches(1.72), Inches(8.05), Inches(4.42))

    # A 2pt accent rule at the top of the rail makes the right column a
    # deliberate object instead of leftover space.
    add_rect(slide, RAIL_X, Inches(1.44), RAIL_W, Emu(28575), _rgb(theme.accent))
    add_text(slide, RAIL_X, Inches(1.68), RAIL_W, Inches(0.26),
             "读法", theme, size=Pt(9.5), color=_rgb(theme.accent),
             bold=True, spacing=2.4)

    readings = [b for b in s.bullets if b]
    if not readings and f:
        readings = _fallback_readings(f)
    y = 2.16
    for i, text in enumerate(readings[:3], 1):
        add_text(slide, RAIL_X, Inches(y - 0.06), Inches(0.46), Inches(0.30),
                 f"{i:02d}", theme, size=Pt(11), color=_rgb(theme.accent),
                 bold=True, spacing=0.8)
        body = subst(text, facts)
        size = _fit(body, Inches(2.52), Inches(0.92), 14.5, 11.5, 1.35)
        add_text(slide, Inches(9.72), Inches(y - 0.10), Inches(2.52), Inches(0.96),
                 body, theme, size=size, color=_rgb(theme.ink), line_spacing=1.35)
        y += 1.06
        if i < min(len(readings), 3):
            add_rect(slide, RAIL_X, Inches(y - 0.24), RAIL_W, Emu(6350), _rgb(theme.hair))

    if f:
        # "销售数据.xlsx / 区域 · 同比增长 按 区域" repeats the dimension twice
        # once the axis already names it; keep file · measure only.
        src = (f.source or "").split(" / ")[0].strip()
        src = re.sub(r"\s*按\s*[^\s]+$", "", src)
        src = f"来源 · {src}" if src else ""
        if f.chart == "line" and f.points and min(v for _, v in f.points) > 0:
            src = f"{src}  ·  纵轴截断，看变化幅度不看绝对高度"
        add_text(slide, MARGIN_X, Inches(6.30), Inches(8.1), Inches(0.26),
                 src, theme, size=Pt(9),
                 color=_rgb(theme.muted), spacing=0.4)
    page_number(slide, idx, theme, total)
    footer_rule(slide, theme)


# --------------------------------------------------------------------------
# table: drawn row by row, so nothing inherits a default table style
# --------------------------------------------------------------------------



def _heat_png(ids: list[str], facts: dict[str, Fact], name: str, theme: Theme) -> str | None:
    """Build a scorecard from several facts that share the same categories."""
    rows_facts = [facts[i] for i in ids if i in facts and facts[i].points]
    if len(rows_facts) < 2:
        return None
    base = [k for k, _ in rows_facts[0].points]
    rows: list[tuple[str, list[float]]] = []
    for cat in base:
        vals: list[float] = []
        ok = True
        for f in rows_facts:
            d = dict(f.points)
            if cat not in d:
                ok = False
                break
            vals.append(d[cat])
        if ok:
            rows.append((cat, vals))
    cols = [f.column or f.label for f in rows_facts]
    units = [f.unit for f in rows_facts]
    return charts2.heatmap(rows, cols, name, theme, w=8.05, h=4.35, unit=units)


def table(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme,
          idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme)
    _head(slide, s, theme, facts)
    ids = _ids(s)
    f = facts.get(ids[0]) if ids else None
    if not f or not f.points:
        page_number(slide, idx, theme, total)
        return

    pts = sorted(f.points, key=lambda kv: -kv[1])[:8]
    x0 = MARGIN_X
    w = CONTENT_W
    y = Inches(1.80)
    # four rows should breathe, eight rows should still fit above the reading
    row_h = Inches(min(0.82, max(0.46, (5.86 - 1.80) / max(len(pts), 1))))
    header_h = Inches(0.38)
    col_rank, col_name, col_bar = 0.45, 2.30, 5.85
    col_conv, col_val, col_gap = 0.95, 1.30, 0.26

    def cell(x, top, width, text, *, size=13, color=None, bold=False, align="left"):
        add_text(slide, x, top, width, Inches(0.30), text, theme, size=Pt(size),
                 color=_rgb(color or theme.ink), bold=bold, align=align)

    add_text(slide, x0, y, Inches(col_rank), header_h, "序", theme, size=Pt(9),
             color=_rgb(theme.muted), bold=True, spacing=1.2)
    raw = f.label if "按" in (f.label or "") else (f.column or f.label)
    head = raw.split("按")[-1].strip() or f.label
    add_text(slide, x0 + Inches(col_rank), y, Inches(col_name), header_h,
             head[:9], theme, size=Pt(9), color=_rgb(theme.muted),
             bold=True, spacing=1.2)
    add_text(slide, x0 + Inches(col_rank + col_name), y, Inches(col_bar), header_h,
             "分布", theme, size=Pt(9), color=_rgb(theme.muted), bold=True, spacing=1.2)
    conv_x = x0 + Inches(w / PX - col_val - col_gap - col_conv)
    add_text(slide, conv_x, y, Inches(col_conv),
             header_h, "转化", theme, size=Pt(9), color=_rgb(theme.muted),
             bold=True, align="right", spacing=1.2)
    add_text(slide, x0 + Inches(w / PX - col_val), y, Inches(col_val), header_h,
             "数值", theme, size=Pt(9), color=_rgb(theme.muted), bold=True,
             align="right", spacing=1.2)

    y += header_h
    add_rect(slide, x0, y, w, Emu(12700), _rgb(theme.ink))

    vmax = max(abs(v) for _, v in pts) or 1
    bar_x = x0 + Inches(col_rank + col_name)
    # Let the bars run all the way to the conversion column: a funnel that only
    # fills 60% of its track reads as timid.
    bar_w = conv_x - bar_x - Inches(0.45)
    has_neg = any(v < 0 for _, v in pts)

    for i, (name, v) in enumerate(pts, 1):
        top = y + Inches(0.18)
        add_text(slide, x0, top - Inches(0.02), Inches(0.4), Inches(0.26),
                 f"{i:02d}", theme, size=Pt(10), color=_rgb(theme.muted), spacing=0.6)
        add_text(slide, x0 + Inches(col_rank), top - Inches(0.03),
                 Inches(col_name - 0.12), Inches(0.30), str(name), theme,
                 size=Pt(13), color=_rgb(theme.ink), bold=(i == 1))
        length = max(0.035, abs(v) / vmax * (0.94 if has_neg else 1.0)) * float(bar_w)
        base = int(float(bar_x) + (float(bar_w) * 0.42 if has_neg else 0))
        if has_neg and v < 0:
            bx = base - int(length)
            color = _rgb(theme.negative)
        else:
            bx = base
            # A hairline-grey bar all but disappears on paper; tint the accent
            # instead so every row still reads as a bar without competing with
            # the highlighted one.
            color = _rgb(theme.accent) if i == 1 else _mix(theme.paper, theme.accent, 0.34)
        bar_h = Inches(0.22)
        add_rect(slide, Emu(bx), int(top) + int((row_h - bar_h) / 2), Emu(int(length)),
                 bar_h, color)
        if i > 1 and pts[i - 2][1]:
            conv = f"{v / pts[i - 2][1] * 100:.0f}%"
        else:
            conv = "—"
        add_text(slide, conv_x, top - Inches(0.03),
                 Inches(col_conv), Inches(0.30), conv, theme, size=Pt(11),
                 color=_rgb(theme.muted), align="right")
        add_text(slide, x0 + Inches(w / PX - col_val), top - Inches(0.03),
                 Inches(col_val), Inches(0.30), _value(v, f.unit),
                 theme, size=Pt(15), color=_rgb(theme.ink if i == 1 else theme.ink2),
                 bold=(i == 1), align="right")
        y += row_h
        add_rect(slide, x0, y, w, Emu(6350), _rgb(theme.hair))

    if has_neg:
        zx = int(bar_x) + int(float(bar_w) * 0.42)
        add_line(slide, zx, Inches(2.18), zx, y, _rgb(theme.muted), 6350)

    # A table without a reading is a data dump. If the plan did not supply
    # one, name the widest drop — still without inventing a number.
    reading = subst(s.note, facts) if s.note else ""
    if not reading and len(pts) >= 2:
        drops = [(pts[i][0], pts[i + 1][0], pts[i][1] - pts[i + 1][1])
                 for i in range(len(pts) - 1)]
        a, b, _ = max(drops, key=lambda t: t[2])
        reading = f"从{a}到{b}收窄最多，{pts[-1][0]}是最低一环。"
    if reading:
        band_top = y + Inches(0.34)
        add_rect(slide, x0, band_top, w, Inches(0.92), _rgb(theme.soft))
        add_rect(slide, x0, band_top, Emu(38100), Inches(0.92), _rgb(theme.accent))
        add_text(slide, Inches(0.92), band_top + Inches(0.16), Inches(1.2),
                 Inches(0.24), "读法", theme, size=Pt(9.5), color=_rgb(theme.accent),
                 bold=True, spacing=2.2)
        add_text(slide, Inches(1.72), band_top + Inches(0.13), Inches(10.6),
                 Inches(0.72), reading, theme, size=Pt(14.5),
                 color=_rgb(theme.ink2), line_spacing=1.35)

    src = (f.source or "").split(" / ")[0]
    if src:
        add_text(slide, x0, Inches(6.92), Inches(9.4), Inches(0.24),
                 f"来源 · {Path(src).name}", theme, size=Pt(9),
                 color=_rgb(theme.muted), spacing=0.6)

    page_number(slide, idx, theme, total)
    footer_rule(slide, theme)


# --------------------------------------------------------------------------
# bullets as numbered tiles
# --------------------------------------------------------------------------


def numbers(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme,
            idx: int, total: int) -> None:
    """Bullets as a numbered argument, not three lonely lines at the top.

    v1 put every bullet in a 4-column tile and left the lower half of the page
    empty. A list of three claims is a vertical argument: give each one a row,
    a number with presence, and enough leading that the eye walks down it.
    """
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme)
    _head(slide, s, theme, facts)

    items = [subst(b, facts) for b in s.bullets if b][:4]
    if not items:
        page_number(slide, idx, theme, total)
        return
    n = len(items)

    if n <= 3:
        # One or two claims on a page look stranded at the top; spread them
        # over the field instead of leaving a 4in hole underneath.
        row = min(1.90, 4.60 / n) if n < 3 else 1.64
        y = 1.86 if n >= 2 else 2.70
        for i, text in enumerate(items):
            add_rect(slide, MARGIN_X, Inches(y), CONTENT_W, Emu(6350),
                     _rgb(theme.accent if i == 0 else theme.hair))
            add_text(slide, MARGIN_X, Inches(y + 0.06), Inches(1.05), Inches(0.62),
                     f"{i + 1:02d}", theme, size=Pt(40),
                     color=_mix(theme.ink, theme.paper, 0.74), bold=True,
                     font="headline", spacing=-1.0)
            lead, body = _split_lead(text)
            top = y + (0.12 if lead else 0.20)
            if lead:
                size = _fit(lead, Inches(10.5), Inches(0.52), 23, 15, 1.1)
                add_text(slide, Inches(1.72), Inches(top), Inches(10.6),
                         Inches(0.52), lead, theme, size=size, color=_rgb(theme.ink),
                         bold=True, font="headline", line_spacing=1.1)
                size = _fit(body, Inches(10.5), Inches(0.72), 15.5, 11, 1.35)
                add_text(slide, Inches(1.72), Inches(top + 0.48), Inches(10.6),
                         Inches(0.74), body, theme, size=size,
                         color=_rgb(theme.ink2), line_spacing=1.4)
            else:
                size = _fit(text, Inches(10.6), Inches(0.95), 22, 15, 1.35)
                add_text(slide, Inches(1.72), Inches(top), Inches(10.6),
                         Inches(0.98), text, theme, size=size, color=_rgb(theme.ink),
                         line_spacing=1.35)
            y += row
    else:
        for i, text in enumerate(items):
            x = colx((i % 2) * 6)
            y = Inches(1.94) + Inches((i // 2) * 2.34)
            add_rect(slide, x, y, Inches(0.72), Emu(28575),
                     _rgb(theme.accent if i == 0 else theme.hair))
            add_text(slide, x, y + Inches(0.18), Inches(0.7), Inches(0.26),
                     f"{i + 1:02d}", theme, size=Pt(10.5), color=_rgb(theme.accent),
                     bold=True, spacing=1.4)
            lead, body = _split_lead(text)
            if lead:
                add_text(slide, x, y + Inches(0.52), col(6), Inches(0.62), lead,
                         theme, size=Pt(20), color=_rgb(theme.ink), bold=True,
                         font="headline", line_spacing=1.15)
                add_text(slide, x, y + Inches(1.16), col(6), Inches(0.92), body,
                         theme, size=Pt(13.5), color=_rgb(theme.ink2), line_spacing=1.4)
            else:
                size = _fit(text, col(6), Inches(1.5), 18, 13, 1.4)
                add_text(slide, x, y + Inches(0.52), col(6), Inches(1.55), text,
                         theme, size=size, color=_rgb(theme.ink), line_spacing=1.4)

    page_number(slide, idx, theme, total)
    footer_rule(slide, theme)


# --------------------------------------------------------------------------
# pictures
# --------------------------------------------------------------------------


def _photo(slide, path: str, left, top, width, height, theme: Theme,
           duotone: bool = True) -> None:
    add_picture(slide, path, left, top, width, height, theme,
                duotone=duotone, darken=0.06 if duotone else 0.0)


def image_full(prs, plan: Plan, s: Slide, facts: dict[str, Fact],
               pics: dict[str, Picture], theme: Theme, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    p = pics.get(s.image or "")
    path = p.path if p else None
    flat = bool(path) and image_is_flat(path)

    if path and not flat:
        _bg(slide, theme, dark=True)
        _photo(slide, path, 0, 0, SLIDE_W, SLIDE_H, theme, duotone=True)
        add_gradient(slide, 0, 0, Inches(8.4), SLIDE_H,
                     _rgb(theme.dark), _rgb(theme.dark), angle=0, alpha1=94, alpha2=0)
        # A bright sky can still swallow the caption on a dark theme; a floor
        # gradient under the text block keeps it readable on any photo.
        add_gradient(slide, 0, Inches(4.20), SLIDE_W, Inches(3.30),
                     _rgb(theme.dark), _rgb(theme.dark), angle=90, alpha1=0, alpha2=86)
        title = subst(s.title, facts)
        size = _fit(title, Inches(7.0), Inches(2.2), 38, 26, 1.1)
        # Anchor the caption block to the bottom-left corner instead of
        # floating it mid-frame: kicker, headline and note read as one stack.
        lines = estimate_lines(title, Inches(7.0), size.pt)
        title_h = lines * size.pt * 1.1 / 72
        note_h = 0.92 if s.note else 0.0
        note_y = 6.46 - note_h
        title_y = note_y - title_h - 0.16
        add_text(slide, MARGIN_X, Inches(title_y - 0.40), Inches(6.0), Inches(0.26),
                 "现场", theme, size=Pt(9.5), color=_rgb(theme.accent),
                 bold=True, spacing=2.6)
        add_text(slide, MARGIN_X, Inches(title_y), Inches(7.1), Inches(title_h + 0.12),
                 title, theme, size=size, color=_rgb(theme.dark_ink), bold=True,
                 font="headline", line_spacing=1.1)
        if s.note:
            add_text(slide, MARGIN_X, Inches(note_y), Inches(6.4), Inches(note_h),
                     subst(s.note, facts), theme, size=Pt(15),
                     color=_mix(theme.dark, theme.dark_ink, 0.86), line_spacing=1.4)
        page_number(slide, idx, theme, total, on_dark=True)
        return

    # A chart, screenshot or diagram: treat it as a document on paper.
    _bg(slide, theme)
    _head(slide, s, theme, facts)
    if path:
        from PIL import Image
        with Image.open(path) as im:
            iw, ih = im.size
        box_w, box_h = Inches(12.09), Inches(4.42)
        scale = min(float(box_w) / iw, float(box_h) / ih)
        w, h = int(iw * scale), int(ih * scale)
        x = int(MARGIN_X + (float(box_w) - w) / 2)
        y = int(Inches(1.74) + (float(box_h) - h) / 2)
        if theme.is_dark:
            pad = int(Inches(0.22))
            add_rect(slide, x - pad, y - pad, Emu(w + 2 * pad), Emu(h + 2 * pad),
                     _rgb("#FFFFFF"))
        else:
            add_rect(slide, x - 9525, y - 9525, Emu(w + 19050), Emu(h + 19050),
                     _rgb(theme.soft))
        slide.shapes.add_picture(path, Emu(x), Emu(y), Emu(w), Emu(h))
    if s.note:
        add_text(slide, MARGIN_X, Inches(6.34), Inches(10.8), Inches(0.36),
                 subst(s.note, facts), theme, size=Pt(11),
                 color=_rgb(theme.muted))
    page_number(slide, idx, theme, total)
    footer_rule(slide, theme)


def split_image(prs, plan: Plan, s: Slide, facts: dict[str, Fact],
                pics: dict[str, Picture], theme: Theme, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme)
    p = pics.get(s.image or "")
    path = p.path if p else None
    flat = bool(path) and image_is_flat(path)

    add_text(slide, MARGIN_X, Inches(0.52), Inches(5.2), Inches(0.26),
             "现场", theme, size=Pt(9.5), color=_rgb(theme.accent),
             bold=True, spacing=2.6)
    title = subst(s.title, facts)
    size = _fit(title, Inches(5.35), Inches(2.15), 31, 21, 1.12)
    title = _wrap_clauses(title, Inches(5.35), size.pt)
    lines = title.count("\n") + 1
    title_h = lines * size.pt * 1.12 / 72.0
    add_text(slide, MARGIN_X, Inches(0.92), Inches(5.42), Inches(title_h + 0.08),
             title, theme, size=size, color=_rgb(theme.ink), bold=True,
             font="headline", line_spacing=1.12)

    y = 0.92 + title_h + 0.34
    if s.note:
        note = subst(s.note, facts)
        size_n = _fit(note, Inches(5.2), Inches(1.45), 15.5, 11, 1.45)
        add_text(slide, MARGIN_X, Inches(y), Inches(5.28), Inches(1.5),
                 note, theme, size=size_n, color=_rgb(theme.ink2), line_spacing=1.45)
        nlines = estimate_lines(note, Inches(5.2), size_n.pt)
        y += nlines * size_n.pt * 1.45 / 72.0 + 0.44
    for b in s.bullets[:3]:
        if y > 5.95:
            break
        text = subst(b, facts)
        add_rect(slide, MARGIN_X, Inches(y + 0.11), Emu(19050), Inches(0.15),
                 _rgb(theme.accent))
        add_text(slide, Inches(0.86), Inches(y), Inches(5.05), Inches(0.62),
                 text, theme, size=Pt(12.5), color=_rgb(theme.ink2), line_spacing=1.35)
        y += 0.70

    if path and not flat:
        _photo(slide, path, Inches(6.78), 0, Inches(6.55), SLIDE_H, theme, duotone=True)
    else:
        # A chart or screenshot is a document. Rather than floating it on a
        # tinted card, print it on a full-height white plate: the page reads as
        # "original material, reproduced" and the chart gets the whole column.
        # True white, not theme.paper: the asset itself is white, and a warm
        # paper plate next to it draws a visible seam around the chart.
        add_rect(slide, Inches(6.78), 0, Inches(6.55), SLIDE_H, _rgb("#FFFFFF"))
        add_rect(slide, Inches(6.78), 0, Emu(28575), SLIDE_H, _rgb(theme.accent))
        if path:
            from PIL import Image
            if image_is_flat(path):
                path, iw, ih = content_path(path)
            else:
                with Image.open(path) as im:
                    iw, ih = im.size
            box_w, box_h = Inches(5.62), Inches(4.62)
            scale = min(float(box_w) / iw, float(box_h) / ih)
            w, h = int(iw * scale), int(ih * scale)
            x = int(Inches(7.20) + (float(box_w) - w) / 2)
            yy = int(Inches(0.82) + (float(box_h) - h) / 2)
            slide.shapes.add_picture(path, Emu(x), Emu(yy), Emu(w), Emu(h))
            # No caption under the plate: the facing column already carries
            # file name, size and provenance. Repeating them is page furniture
            # pretending to be information.

    # The left column rarely fills a tall page; a small provenance block is
    # more useful than stretching the note or leaving a hole.
    if p and not s.bullets and path:
        from PIL import Image
        with Image.open(path) as im:
            iw, ih = im.size
        kind_zh = {"chart": "图表", "screenshot": "截图", "logo": "标识",
                   "page": "文档页", "flat": "图形", "photo": "照片"}.get(p.kind, "图片")
        rows = [("文件", p.name), ("尺寸", f"{iw} × {ih} px"),
                ("来源", f"{kind_zh} · 原始文件，未二次加工")]
        # Follow the copy, but never crowd it: the passport block sits under
        # the note when there is room, and rides the foot of the column when
        # the copy runs long.
        my = max(3.62, min(5.55, y + 0.62))
        add_rect(slide, MARGIN_X, Inches(my - 0.18), Inches(5.30), Emu(9525),
                 _rgb(theme.hair))
        for label, value in rows:
            add_text(slide, MARGIN_X, Inches(my), Inches(0.72), Inches(0.26),
                     label, theme, size=Pt(8.5), color=_rgb(theme.muted), spacing=1.6)
            add_text(slide, Inches(1.34), Inches(my - 0.035), Inches(4.55), Inches(0.3),
                     value, theme, size=Pt(10.5), color=_rgb(theme.ink2))
            my += 0.40

    page_number(slide, idx, theme, total, x=Inches(5.0))


# --------------------------------------------------------------------------
# quote: let the client's own words carry one beat
# --------------------------------------------------------------------------


def _quote_lines(text: str, width, size_pt: float) -> str:
    """Wrap a quote at clause boundaries and never leave a mark hanging.

    `_wrap_clauses` already refuses to split a word across lines; all that is
    left is to drop the trailing comma/period on each line. The pause is
    carried by the break itself, which is how display quotes are set.
    """
    lines = _wrap_clauses(text, width, size_pt)
    return "\n".join(line.rstrip("，。；：、") for line in lines.split("\n") if line.strip())


def notes(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme,
          idx: int, total: int) -> None:
    """Extracts from the user's documents, set as a page of quotations.

    Used when there are no numbers to chart. One or two sentences get the room
    of a pull quote; three or four become a short ledger. Either way the page
    is filled by type, not by an empty chart frame.
    """
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme)

    add_text(slide, MARGIN_X, Inches(0.40), Inches(4.0), Inches(0.26),
             "原文摘录", theme, size=Pt(10), color=_rgb(theme.accent),
             bold=True, spacing=1.6)
    size = _fit(s.title, Inches(11.2), Inches(0.72), 27, 20, 1.2)
    add_text(slide, MARGIN_X, Inches(0.62), Inches(11.2), Inches(0.78),
             s.title, theme, size=size, color=_rgb(theme.ink2),
             font="headline", line_spacing=1.2)
    add_rect(slide, MARGIN_X, Inches(1.44), Inches(1.44), Emu(19050), _rgb(theme.accent))

    items = [subst(b, facts) for b in s.bullets if b][:4]
    if not items:
        page_number(slide, idx, theme, total)
        return
    n = len(items)
    body_pt = {1: 34.0, 2: 26.0}.get(n, 21.0)
    rows = []
    for text in items:
        lead, body = _split_lead(text)
        line_h = estimate_lines(lead, Inches(11.2), body_pt) * body_pt * 1.58 / 72.0
        rows.append((lead, body, line_h))
    gap = 0.44
    block = sum(r[2] + (0.30 if r[1] else 0.0) for r in rows) + gap * (n - 1)
    y = 1.92 + max(0.0, (4.30 - block) * 0.42)
    for i, (lead, body, line_h) in enumerate(rows):
        if i:
            add_rect(slide, MARGIN_X, Inches(y - gap * 0.52), CONTENT_W,
                     Emu(6350), _rgb(theme.hair))
        add_text(slide, MARGIN_X, Inches(y), Inches(11.2), Inches(line_h + 0.10),
                 lead, theme, size=Pt(body_pt), color=_rgb(theme.ink),
                 line_spacing=1.38)
        if body:
            add_text(slide, MARGIN_X, Inches(y + line_h + 0.06), Inches(11.2),
                     Inches(0.28), body, theme, size=Pt(10.5),
                     color=_rgb(theme.accent), spacing=0.8)
        y += line_h + (0.30 if body else 0.0) + gap

    if s.note:
        add_text(slide, MARGIN_X, Inches(6.30), Inches(11.2), Inches(0.30),
                 s.note, theme, size=Pt(10.5), color=_rgb(theme.muted))
    page_number(slide, idx, theme, total)


def compare(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme,
            idx: int, total: int) -> None:
    """Two states of the same business, side by side.

    A ratio chart shows every region at once; this page shows the only two that
    matter — the one pulling and the one bleeding — and lets the gap between
    them carry the argument.
    """
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme)
    _head(slide, s, theme, facts)

    sides = []
    for raw in (s.bullets or [])[:2]:
        label, body = _split_lead(subst(raw, facts))
        sides.append((label, body))
    while len(sides) < 2:
        sides.append(("", ""))

    left_x, right_x = MARGIN_X, Inches(7.02)
    col_w = Inches(5.62)
    add_rect(slide, Inches(6.72), Inches(1.86), Emu(9525), Inches(3.30),
             _rgb(theme.hair))
    for k, (label, body) in enumerate(sides):
        x = left_x if k == 0 else right_x
        tone = theme.muted if k == 0 else theme.accent
        add_text(slide, x, Inches(1.92), col_w, Inches(0.30),
                 label or ("现在" if k == 0 else "接下来"), theme, size=Pt(10.5),
                 color=_rgb(tone), bold=True, spacing=1.4)
        # One region per line: a comma-separated run reads as a sentence and
        # hides the comparison, which is the whole point of the page.
        bits = [b.strip() for b in re.split(r"[、,，]", body) if b.strip()]
        body = "\n".join(bits) if 1 < len(bits) <= 3 else body
        size = _fit(body, col_w, Inches(3.10), 40, 20, 1.55)
        add_text(slide, x, Inches(2.34), col_w, Inches(3.10),
                 body, theme, size=size,
                 color=_rgb(theme.ink), bold=True, font="headline",
                 line_spacing=1.55)
    if s.note:
        add_rect(slide, MARGIN_X, Inches(5.72), Inches(12.09), Inches(0.86),
                 _rgb(theme.soft))
        add_rect(slide, MARGIN_X, Inches(5.72), Emu(28575), Inches(0.86),
                 _rgb(theme.accent))
        add_text(slide, Inches(0.86), Inches(5.90), Inches(11.40), Inches(0.54),
                 s.note, theme, size=Pt(15), color=_rgb(theme.ink2),
                 line_spacing=1.3)
    page_number(slide, idx, theme, total)


def quote(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme,
          idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme, dark=True)
    add_gradient(slide, 0, 0, SLIDE_W, SLIDE_H,
                 _rgb(theme.dark), _mix(theme.dark, theme.accent, 0.18), angle=160)

    add_text(slide, MARGIN_X, Inches(1.02), Inches(1.4), Inches(1.0),
             "“", theme, size=Pt(150), color=_mix(theme.dark, theme.accent, 0.55),
             bold=True, font="headline", line_spacing=1.0)

    text = subst(s.title, facts)
    size = _fit(text, Inches(10.4), Inches(3.4), 40, 24, 1.42)
    # Keep the line length readable: a quote set too wide loses its rhythm.
    lines = _quote_lines(text, Inches(10.2), size.pt)
    add_text(slide, MARGIN_X, Inches(2.02), Inches(10.6), Inches(3.5),
             lines, theme, size=size, color=_rgb(theme.dark_ink),
             font="headline", line_spacing=1.42)

    add_rect(slide, MARGIN_X, Inches(5.94), Inches(0.86), Emu(38100),
             _rgb(theme.accent))
    if s.note:
        add_text(slide, MARGIN_X, Inches(6.14), Inches(7.4), Inches(0.30),
                 subst(s.note, facts), theme, size=Pt(11.5),
                 color=_rgb(theme.dark_muted))
    page_number(slide, idx, theme, total)


# --------------------------------------------------------------------------
# closing
# --------------------------------------------------------------------------


def closing(prs, plan: Plan, s: Slide, facts: dict[str, Fact], theme: Theme, idx: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(slide, theme, dark=True)
    add_gradient(slide, 0, 0, SLIDE_W, SLIDE_H,
                 _rgb(theme.dark), _mix(theme.dark, theme.accent, 0.26), angle=200)

    add_text(slide, MARGIN_X, Inches(0.62), Inches(5.0), Inches(0.26),
             "下一步", theme, size=Pt(10), color=_rgb(theme.accent),
             bold=True, spacing=2.8)
    title = subst(s.title, facts)
    size = _fit(title, Inches(4.55), Inches(2.05), 40, 24, 1.05)
    title = _wrap_clauses(title, Inches(4.55), size.pt)
    add_text(slide, MARGIN_X, Inches(1.02), Inches(4.90), Inches(2.1),
             title, theme, size=size, color=_rgb(theme.dark_ink), bold=True,
             font="headline", line_spacing=1.05)

    add_rect(slide, MARGIN_X, Inches(2.62), Inches(4.55), Emu(6350),
             _mix(theme.dark, "#FFFFFF", 0.26))
    add_text(slide, MARGIN_X, Inches(2.86), Inches(4.9), Inches(1.5),
             "把这一页的判断带进下一个季度：\n谁负责、什么时候、看哪个数。",
             theme, size=Pt(15.5), color=_rgb(theme.dark_muted), line_spacing=1.55)

    items = [subst(b, facts) for b in s.bullets if b][:3]
    if items:
        px, pw = Inches(6.15), Inches(5.95)
        add_text(slide, px, Inches(0.94), pw, Inches(0.26),
                 "按季度推进", theme, size=Pt(10),
                 color=_rgb(theme.dark_muted), spacing=2.4)
        for i, text in enumerate(items):
            y = 1.46 + i * 1.52
            add_rect(slide, px, Inches(y), pw, Emu(9525),
                     _mix(theme.dark, "#FFFFFF", 0.30))
            add_text(slide, px, Inches(y + 0.20), Inches(0.62), Inches(0.24),
                     f"{i + 1:02d}", theme, size=Pt(11),
                     color=_rgb(theme.accent), bold=True, spacing=1.4)
            size_c = 34 if len(text) <= 8 else 21
            body = _wrap_clauses(text, Inches(4.95), size_c)
            add_text(slide, px + Inches(0.66), Inches(y + 0.06),
                     Inches(5.10), Inches(1.30), body, theme, size=Pt(size_c),
                     color=_rgb(theme.dark_ink), line_spacing=1.25)
    elif s.note:
        add_text(slide, MARGIN_X, Inches(3.8), Inches(9.4), Inches(1.2),
                 subst(s.note, facts), theme, size=Pt(19),
                 color=_rgb(theme.dark_muted), line_spacing=1.4)

    add_rect(slide, MARGIN_X, Inches(6.42), CONTENT_W, Emu(6350),
             _mix(theme.dark, "#FFFFFF", 0.22))
    add_text(slide, MARGIN_X, Inches(6.58), Inches(7.0), Inches(0.24),
             plan.deck_title, theme, size=Pt(9.5), color=_rgb(theme.dark_muted),
             spacing=0.8)
    page_number(slide, idx, theme)


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------


def _speaker_notes(slide, s: Slide, facts: dict[str, Fact]) -> None:
    """What the presenter says, not what the slide already says.

    A deck without notes forces the author to reconstruct the argument in the
    room. Three lines per page is enough: the claim, the evidence behind it,
    and the move the audience should make.
    """
    try:
        frame = slide.notes_slide.notes_text_frame
    except Exception:  # noqa: BLE001 - notes are optional, never fatal
        return
    title = subst(s.title, facts)
    lines = [title]
    ids = re.findall(r"\{\{(f\d+)\}\}", s.title + " " + " ".join(s.bullets))
    ids += [x.strip() for x in (s.fact or "").split(",") if x.strip()]
    used = [facts[fid] for fid in dict.fromkeys(ids) if fid in facts]
    if used:
        ev = "；".join(f"{f.label}：{f.value}" for f in used[:3])
        lines.append(f"依据 {ev}（{used[0].source}）。")
    if s.note:
        lines.append(subst(s.note, facts))
    if s.bullets:
        lines.append("要点：" + " / ".join(subst(b, facts) for b in s.bullets[:3]))
    frame.text = "\n".join(lines)


def build(plan: Plan, facts: list[Fact], pictures: list[Picture], out: str,
          theme: str | None = None) -> str:
    fmap = {f.id: f for f in facts}
    pmap = {p.id: p for p in pictures}
    th = get_theme(theme)
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    total = len(plan.slides)
    sec = 0

    for i, s in enumerate(plan.slides, 1):
        before = len(prs.slides._sldIdLst)
        if s.layout == "title":
            cover(prs, plan, s, fmap, th, pmap)
        elif s.layout == "section":
            sec += 1
            section(prs, plan, s, fmap, th, i, sec)
        elif s.layout == "key_figure":
            statement(prs, plan, s, fmap, th, i, total)
        elif s.layout == "kpi_row":
            kpi(prs, plan, s, fmap, th, i, total)
        elif s.layout == "chart":
            chart(prs, plan, s, fmap, th, i, total)
        elif s.layout == "table":
            table(prs, plan, s, fmap, th, i, total)
        elif s.layout == "bullets":
            numbers(prs, plan, s, fmap, th, i, total)
        elif s.layout == "image_full":
            image_full(prs, plan, s, fmap, pmap, th, i, total)
        elif s.layout == "split_image":
            split_image(prs, plan, s, fmap, pmap, th, i, total)
        elif s.layout == "compare":
            compare(prs, plan, s, fmap, th, i, total)
        elif s.layout == "notes":
            notes(prs, plan, s, fmap, th, i, total)
        elif s.layout == "quote":
            quote(prs, plan, s, fmap, th, i, total)
        elif s.layout == "closing":
            closing(prs, plan, s, fmap, th, i)
        else:
            numbers(prs, plan, s, fmap, th, i, total)
        if len(prs.slides._sldIdLst) > before:
            _speaker_notes(prs.slides[len(prs.slides._sldIdLst) - 1], s, fmap)

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out

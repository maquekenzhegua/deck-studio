"""Text primitives.

Two things live here that python-pptx does not give you:

1. CJK-correct font assignment. `run.font.name` only sets the latin typeface;
   Chinese glyphs fall back to whatever the theme says, which on a fresh deck
   is often a serif. We set `a:ea` explicitly.
2. A text metric estimator. python-pptx never lays out text, so it cannot tell
   you a headline overflows its box. We estimate width from character classes
   and report overflow instead of silently shipping a broken slide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.dml.color import RGBColor

from .theme import FONT_CJK, FONT_LATIN

_ALIGN = {
    "left": PP_ALIGN.LEFT,
    "center": PP_ALIGN.CENTER,
    "right": PP_ALIGN.RIGHT,
}

_ANCHOR = {
    "top": MSO_ANCHOR.TOP,
    "middle": MSO_ANCHOR.MIDDLE,
    "bottom": MSO_ANCHOR.BOTTOM,
}


def set_font(run, size, color, bold: bool = False, latin: str = FONT_LATIN) -> None:
    """Assign a font that works for both latin and CJK runs."""
    f = run.font
    f.size = size
    f.bold = bold
    f.name = latin
    f.color.rgb = color
    rpr = run._r.get_or_add_rPr()
    for tag, face in (("a:ea", FONT_CJK), ("a:cs", FONT_CJK)):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", face)


def add_text(
    slide,
    left: Emu,
    top: Emu,
    width: Emu,
    height: Emu,
    text: str,
    *,
    size=Pt(17),
    color=None,
    bold: bool = False,
    align: str = "left",
    anchor: str = "top",
    line_spacing: float = 1.22,
    space_after: int = 0,
):
    """A single-style text block. Returns the shape."""
    from .theme import INK

    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = _ANCHOR[anchor]

    lines = text.split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = _ALIGN[align]
        p.line_spacing = line_spacing
        if space_after and i < len(lines) - 1:
            p.space_after = Pt(space_after)
        run = p.add_run()
        run.text = line
        set_font(run, size, color if color is not None else INK, bold)
    return box


def add_bullets(
    slide,
    left: Emu,
    top: Emu,
    width: Emu,
    height: Emu,
    items: list[str],
    *,
    size=Pt(17),
    color=None,
    gap: int = 10,
    marker_color=None,
):
    """Bulleted list with a drawn marker rather than a bullet glyph.

    PowerPoint's default bullet sits too close to the text and cannot be
    coloured independently, which is why generated decks look like Word.
    """
    from .theme import INK, ACCENT, FS_BODY

    size = size or FS_BODY
    color = color if color is not None else INK
    marker = marker_color if marker_color is not None else ACCENT
    dot = Pt(5)
    indent = Pt(20)

    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0

    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = 1.24
        if i:
            p.space_before = Pt(gap)
        # Every line starts at the indent; the marker lives in the gutter to
        # its left. A negative first-line offset here would pull line one back
        # to zero and collide with the marker.
        # `paragraph.left_indent` is a no-op on python-pptx 1.0.2, so the
        # attribute is written directly.
        ppr = p._p.get_or_add_pPr()
        ppr.set("marL", str(int(indent)))
        ppr.set("indent", "0")
        run = p.add_run()
        run.text = item
        set_font(run, size, color, False)

    # Draw the markers as separate shapes so they can be accent-coloured.
    # `gap` is in points; the cursor is in EMU, so it has to be converted or
    # every marker drifts up by a line's worth over a long list.
    y = top
    for item in items:
        m = slide.shapes.add_shape(1, left, Emu(int(y + size * 0.52)), dot, dot)
        m.fill.solid()
        m.fill.fore_color.rgb = marker
        m.line.fill.background()
        m.shadow.inherit = False
        lines = estimate_lines(item, width - indent, size)
        y = Emu(int(y + lines * size * 1.24 + gap * 12700))
    return box


def add_numbered_list(
    slide,
    left: Emu,
    top: Emu,
    width: Emu,
    height: Emu,
    items: list[str],
    *,
    size=Pt(24),
    color=None,
    rule_color=None,
    number_color=None,
):
    """A list of claims, one per row, separated by hairlines.

    Three short bullets floating in the top-left corner is the single most
    recognisable "a model wrote this" shape. Giving each claim its own row,
    its own number and a rule above it makes the slide read as a sequence of
    assertions rather than a text dump.
    """
    from .theme import ACCENT, HAIRLINE, INK, FS_CAPTION

    color = color if color is not None else INK
    rule = rule_color if rule_color is not None else HAIRLINE
    num_color = number_color if number_color is not None else ACCENT

    if not items:
        return None

    row_h = int(float(height) / len(items))
    num_w = Pt(34)
    text_x = Emu(int(left + num_w))
    text_w = Emu(int(float(width) - float(num_w)))

    for i, item in enumerate(items, 1):
        y = Emu(int(top) + row_h * (i - 1))
        # Hairline above every row except the first: it is the sequence
        # marker, and it stops the eye from reading the rows as a paragraph.
        if i > 1:
            add_rect(slide, left, Emu(int(y) - 1), width, Emu(9525), fill=rule)
        add_text(slide, left, Emu(int(y) + int(row_h * 0.22)), num_w, Inches(0.34),
                 f"{i:02d}", size=FS_CAPTION, color=num_color, bold=True)
        add_text(slide, text_x, Emu(int(y) + int(row_h * 0.14)), text_w,
                 Emu(int(row_h * 0.72)), item, size=size, color=color, line_spacing=1.28)
    return None


def add_rule(slide, left: Emu, top: Emu, width: Emu, *, color=None, height=None):
    """A short horizontal rule. Used as the section marker."""
    from .theme import ACCENT, RULE_H

    bar = slide.shapes.add_shape(1, left, top, width, height or RULE_H)
    bar.fill.solid()
    bar.fill.fore_color.rgb = color if color is not None else ACCENT
    bar.line.fill.background()
    bar.shadow.inherit = False
    return bar


def add_rect(slide, left, top, width, height, *, fill, line=None, radius=None):
    shape = slide.shapes.add_shape(1, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)
    shape.shadow.inherit = False
    return shape


# --- metrics --------------------------------------------------------------

def char_width(ch: str) -> float:
    """Width of one character in em units."""
    o = ord(ch)
    if o < 0x2E80:
        if ch == " ":
            return 0.28
        if ch in "iljI.,:;'|!":
            return 0.30
        if ch in "mwMW@%":
            return 0.92
        if ch.isupper():
            return 0.66
        if ch.isdigit():
            return 0.56
        return 0.53
    if o < 0xFF00:  # CJK ideographs, kana, hangul
        return 1.0
    return 0.55  # fullwidth punctuation


def text_width(text: str, size) -> float:
    """Approximate rendered width in EMU for a single line."""
    em = size.pt * 12700  # 1pt = 12700 EMU
    return sum(char_width(c) for c in text) * em


def estimate_lines(text: str, width: Emu, size) -> int:
    """How many wrapped lines this text needs in a box of `width`."""
    if not text:
        return 1
    avail = float(width)
    total = 0.0
    lines = 1
    for ch in text:
        w = char_width(ch) * size.pt * 12700
        if total + w > avail and total > 0:
            lines += 1
            total = 0.0
        total += w
    return lines


def estimate_height(text: str, width: Emu, size, line_spacing: float = 1.22) -> float:
    return estimate_lines(text, width, size) * size.pt * 12700 * line_spacing


@dataclass
class Overflow:
    where: str
    need_in: float
    have_in: float

    def __str__(self) -> str:
        return f"{self.where}: needs {self.need_in:.2f}in, box is {self.have_in:.2f}in"

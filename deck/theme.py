"""Design tokens.

The single biggest cause of "AI 味" is a generator that is allowed to choose
anything. So nothing here is chosen at render time: type scale, grid, palette
and spacing are fixed constants, and layouts may only pick from them.

Everything downstream imports from this module and is forbidden from inventing
a font size or a colour.

Two decisions worth knowing:

* The type scale is a *report* scale, not a *pitch* scale. A 40pt headline on a
  13.3in slide is a billboard; McKinsey-class decks run 24–28pt with 13–14pt
  body. Bigger type means less content per page, which is what makes generated
  decks read as empty rather than authoritative.
* There are two colours with meaning: PRIMARY carries structure, ALERT means
  "this is the problem". A deck that accents everything accents nothing.
"""

from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

# --- canvas ---------------------------------------------------------------
SLIDE_W = Inches(13.333)  # 16:9
SLIDE_H = Inches(7.5)

# 12-column grid. Every layout places content on these lines, never on
# hand-tuned coordinates, so slides line up across the whole deck.
MARGIN_X = Inches(0.75)
MARGIN_TOP = Inches(0.52)
MARGIN_BOTTOM = Inches(0.46)
GUTTER = Inches(0.20)

CONTENT_W = SLIDE_W - 2 * MARGIN_X
CONTENT_H = SLIDE_H - MARGIN_TOP - MARGIN_BOTTOM
COL_W = int((CONTENT_W - 11 * GUTTER) / 12)


def col(n: int) -> Emu:
    """Width of n grid columns."""
    return Emu(int(n * COL_W + (n - 1) * GUTTER))


def colx(n: int) -> Emu:
    """Left edge of column n (0-based)."""
    return Emu(int(MARGIN_X + n * (COL_W + GUTTER)))


# --- type scale -----------------------------------------------------------
# Floors from the consulting reference: body >= 14pt, table/axis >= 12pt,
# footnote >= 10pt. Never shrink below a floor to make text fit — cut words or
# split the slide instead.
FS_COVER = Pt(38)     # cover only
FS_TITLE = Pt(26)     # slide headline
FS_KPI = Pt(86)
FS_KPI_MD = Pt(60)
FS_HERO = Pt(50)       # the single big number on a key-figure slide
FS_LEAD = Pt(18)      # one sentence under the headline
FS_SECTION = Pt(15)   # eyebrow / section label / table header
FS_BODY = Pt(14)      # body, card text
FS_CAPTION = Pt(11)   # captions, axis labels
FS_FOOT = Pt(9)       # source lines, page numbers

# --- palette --------------------------------------------------------------
PRIMARY = RGBColor(0x16, 0x32, 0x5C)      # deep navy: structure, cover field
PRIMARY_MID = RGBColor(0x3D, 0x5E, 0x8C)
PRIMARY_SOFT = RGBColor(0xE9, 0xEE, 0xF6)  # tint for bands and table headers
ALERT = RGBColor(0xA8, 0x3A, 0x2C)        # "this is the problem". Use once.
ALERT_SOFT = RGBColor(0xF6, 0xE9, 0xE6)
DATA_BAR = RGBColor(0xD8, 0xDB, 0xDF)   # in-table bars: a magnitude cue, not an accent

INK = RGBColor(0x11, 0x14, 0x1A)      # near-black, softer than pure black
INK_2 = RGBColor(0x44, 0x4B, 0x56)    # secondary text
MUTED = RGBColor(0x83, 0x8B, 0x98)    # captions, rules
HAIRLINE = RGBColor(0xD8, 0xDD, 0xE5)  # separators
PAPER = RGBColor(0xFF, 0xFF, 0xFF)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

# Backwards-compatible aliases — earlier layouts import these names.
ACCENT = PRIMARY
ACCENT_SOFT = PRIMARY_SOFT

# Chart series palette: primary, then neutral steps. Never rainbow. The last
# entry is deliberately grey because "everything else" should look like
# everything else.
CHART_SERIES = ["16325C", "7D95B8", "B9C4D4", "8B939F", "D8DDE5"]
CHART_ALERT = "A83A2C"
CHART_GRID = "E3E6EB"

# --- typography -----------------------------------------------------------
FONT_LATIN = "Segoe UI"
FONT_CJK = "Microsoft YaHei"

# --- layout rhythm --------------------------------------------------------
RULE_W = Inches(0.9)      # short rule under a section title
RULE_H = Pt(3)
CARD_RADIUS = 0.04

TITLE_TOP = MARGIN_TOP
TITLE_H = Inches(0.72)
RULE_TOP = Inches(1.36)
BODY_TOP = Inches(1.62)
BODY_H = SLIDE_H - BODY_TOP - MARGIN_BOTTOM - Inches(0.28)
FOOT_TOP = SLIDE_H - MARGIN_BOTTOM - Inches(0.10)

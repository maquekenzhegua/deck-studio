"""Design tokens.

The single biggest cause of "AI 味" is a generator that is allowed to choose
anything. So nothing here is chosen at render time: type scale, grid, palette
and spacing are fixed constants, and layouts may only pick from them.

Everything downstream imports from this module and is forbidden from inventing
a font size or a colour.
"""

from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

# --- canvas ---------------------------------------------------------------
SLIDE_W = Inches(13.333)  # 16:9
SLIDE_H = Inches(7.5)

# 12-column grid. Every layout places content on these lines, never on
# hand-tuned coordinates, so slides line up across the whole deck.
MARGIN_X = Inches(0.85)
MARGIN_TOP = Inches(0.62)
MARGIN_BOTTOM = Inches(0.55)
GUTTER = Inches(0.22)

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
# Five sizes. A slide may use at most three of them.
FS_TITLE = Pt(40)
FS_SECTION = Pt(26)
FS_BODY = Pt(17)
FS_CAPTION = Pt(12)
FS_FOOT = Pt(9)

# --- palette --------------------------------------------------------------
# One accent. That is the whole colour system. Multi-colour decks read as
# decoration; a single accent reads as intent.
INK = RGBColor(0x11, 0x14, 0x1A)      # near-black, softer than pure black
INK_2 = RGBColor(0x4A, 0x51, 0x5C)    # secondary text
MUTED = RGBColor(0x8B, 0x93, 0x9F)    # captions, rules
HAIRLINE = RGBColor(0xE3, 0xE6, 0xEB)  # separators
PAPER = RGBColor(0xFF, 0xFF, 0xFF)
ACCENT = RGBColor(0x1F, 0x5A, 0xFF)   # the one accent
ACCENT_SOFT = RGBColor(0xE8, 0xEE, 0xFF)  # accent tint for backgrounds

# Chart series palette: accent first, then neutral steps. Never rainbow.
CHART_SERIES = ["1F5AFF", "8FA8FF", "11141A", "8B939F", "C9D2E3"]

# --- typography -----------------------------------------------------------
FONT_LATIN = "Segoe UI"
FONT_CJK = "Microsoft YaHei"

# --- layout rhythm --------------------------------------------------------
RULE_W = Inches(0.9)      # short rule under a section title
RULE_H = Pt(3)
CARD_RADIUS = 0.04

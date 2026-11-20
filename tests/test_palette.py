"""The template palette: a deck's own colours, mapped onto our six roles."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deck import palette  # noqa: E402

OFFICE = {
    "dk1": "#000000", "lt1": "#FFFFFF",
    "accent1": "#4F81BD", "accent2": "#C0504D",
    "accent3": "#9BBB59", "accent4": "#8064A2",
}


class PaletteTest(unittest.TestCase):
    def test_light_scheme_keeps_paper_and_brand_accent(self):
        t = palette.build(OFFICE, {})
        self.assertEqual(t.paper, "#FFFFFF")
        self.assertEqual(t.accent, "#4F81BD")     # accent1 is the brand
        self.assertNotEqual(t.accent2, t.accent)
        self.assertGreater(palette.contrast(t.ink, t.paper), 7)

    def test_dark_scheme_is_flipped_not_inverted_text(self):
        dark = {"dk1": "#FFFFFF", "lt1": "#101820", "accent1": "#5EEAD4"}
        t = palette.build(dark, {})
        self.assertLess(palette.luma(t.paper), 0.25)   # page stays dark
        self.assertGreater(palette.luma(t.ink), 0.6)   # type stays light
        self.assertGreater(palette.contrast(t.dark_ink, t.dark), 3)

    def test_unreadable_accent_falls_back_to_a_contrasting_one(self):
        weak = {"dk1": "#111111", "lt1": "#FFFFFF", "accent1": "#F4F4F4",
                "accent2": "#1F5FBF"}
        t = palette.build(weak, {})
        self.assertEqual(t.accent, "#1F5FBF")
        self.assertGreater(palette.contrast(t.accent, t.paper), 3)

    def test_serif_font_scheme_sets_the_flag(self):
        t = palette.build(OFFICE, {"majorFont": {"latin": "Georgia", "ea": "宋体"}})
        self.assertTrue(t.serif_headline)

    def test_missing_font_is_not_used(self):
        t = palette.build(OFFICE, {"majorFont": {"latin": "NoSuchFontXYZ"}})
        self.assertNotEqual(t.headline_latin, "NoSuchFontXYZ")

    def test_title_size_is_adopted_but_brought_into_range(self):
        t = palette.build(OFFICE, {}, title_pt=44)
        self.assertAlmostEqual(t.head_pt, 30, delta=1)
        big = palette.build(OFFICE, {}, title_pt=96)
        self.assertLessEqual(big.head_pt, 33)
        self.assertEqual(palette.build(OFFICE, {}).head_pt, 0)

    def test_every_role_has_a_hex_colour(self):
        t = palette.build(OFFICE, {})
        for field in ("paper", "ink", "ink2", "muted", "hair", "soft",
                      "accent", "accent2", "dark", "dark_ink", "dark_muted"):
            value = getattr(t, field)
            self.assertRegex(value, r"^#[0-9A-F]{6}$", field)


if __name__ == "__main__":
    unittest.main()

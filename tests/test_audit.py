"""The audit rules are code, so they get tests like code.

These build tiny decks in a temp dir rather than trusting the sample: a rule
that only ever sees one deck is a rule that will fire on the second one.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pptx import Presentation  # noqa: E402
from pptx.util import Inches, Pt  # noqa: E402

from deck import audit  # noqa: E402
from deck.theme import CONTENT_W, MARGIN_X, SLIDE_H, SLIDE_W  # noqa: E402

MX = float(MARGIN_X) / 914400.0        # left margin, in inches
CW = float(CONTENT_W) / 914400.0       # content width, in inches


def deck(slides: list[list[tuple[str, float, float, float, float, int]]]) -> str:
    """Build a .pptx from [(text, left, top, width, height, size_pt)] per slide."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    for spec in slides:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        for text, l, t, w, h, pt in spec:
            box = s.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
            run = box.text_frame.paragraphs[0].add_run()
            run.text = text
            run.font.size = Pt(pt)
    out = Path(tempfile.mkdtemp()) / "t.pptx"
    prs.save(out)
    return str(out)


TITLE = ("增长由华东独撑，华北已转为负增长", MX, 0.52, CW, 1.0, 26)


class TestTypeScale(unittest.TestCase):
    def test_off_scale_size_is_caught(self):
        path = deck([
            [TITLE, ("华东贡献了全部增量", MX, 2.12, 8.7, 1.0, 17)],
            [("收入向服务倾斜", MX, 0.52, CW, 1.0, 26),
             ("结构改善但增速放缓", MX, 2.12, 3.5, 1.5, 19)],
        ])
        rep = audit.audit(path)
        pages = [p[1] for p in rep.problems if p[0] == "R03"]
        self.assertIn(2, pages)
        self.assertEqual(rep.slides[1].off_scale, [19.0])

    def test_five_sizes_are_within_measurement(self):
        """The corpus runs a median of 3 sizes per page and a p90 of 7. Five is
        an ordinary report page: headline, lead, body, caption, source."""
        path = deck([
            [TITLE,
             ("一句话引导", MX, 2.12, 8.0, 0.6, 20),
             ("正文要点", MX, 3.0, 8.0, 0.6, 14),
             ("注释", MX, 4.0, 8.0, 0.4, 12),
             ("小注", MX, 4.6, 8.0, 0.4, 11)],
            [("结论", MX, 0.52, CW, 1.0, 26),
             ("正文", MX, 2.12, 8.0, 0.6, 14)],
        ])
        rep = audit.audit(path)
        self.assertEqual(rep.slides[0].n_sizes, 5)
        # 20pt and 12pt are off the theme scale, so R03 fires for that reason;
        # what must not fire is the count rule.
        self.assertFalse(any("种字号" in d for r, _, d in rep.problems if r == "R03"))

    def test_six_sizes_on_one_slide_is_caught(self):
        path = deck([
            [TITLE,
             ("一句话引导", MX, 2.12, 8.0, 0.6, 20),
             ("正文要点", MX, 2.9, 8.0, 0.6, 14),
             ("注释", MX, 3.7, 8.0, 0.4, 12),
             ("小注", MX, 4.3, 8.0, 0.4, 11),
             ("极小的标注", MX, 5.0, 8.0, 0.4, 9.5)],
            [("结论", MX, 0.52, CW, 1.0, 26),
             ("正文", MX, 2.12, 8.0, 0.6, 14)],
        ])
        rep = audit.audit(path)
        self.assertEqual(rep.slides[0].n_sizes, 6)
        self.assertTrue(any("6 种字号" in d for r, _, d in rep.problems if r == "R03"))

    def test_footer_size_is_not_counted_as_a_level(self):
        """Every page carries a 9pt page number. Counting it would make R03 a
        page counter that fires on any deck with a footer."""
        path = deck([
            [TITLE,
             ("正文要点", MX, 3.0, 8.0, 0.6, 14),
             ("9", 12.0, 6.94, 0.4, 0.2, 9)],
        ])
        rep = audit.audit(path)
        self.assertEqual(rep.slides[0].n_sizes, 2)


class TestGeometry(unittest.TestCase):
    def test_box_on_margin_snaps(self):
        path = deck([
            [TITLE, ("正文", MX, 2.12, 8.0, 0.6, 14)],
            [("结论", MX, 0.52, CW, 1.0, 26), ("正文", MX, 2.12, 8.0, 0.6, 14)],
        ])
        rep = audit.audit(path)
        self.assertEqual(rep.slides[0].snap, 1.0)

    def test_hand_placed_box_breaks_snap(self):
        path = deck([
            [TITLE, ("正文", MX + 0.37, 2.12, 8.0, 0.6, 14)],
            [("结论", MX, 0.52, CW, 1.0, 26), ("正文", MX - 0.30, 2.12, 8.0, 0.6, 14)],
        ])
        rep = audit.audit(path)
        self.assertLess(rep.slides[0].snap, 1.0)


class TestNarrative(unittest.TestCase):
    def test_repeated_claim_is_flagged(self):
        path = deck([
            [("增长由华东独撑，华北已转为负增长", MX, 0.52, CW, 1.0, 26),
             ("正文", MX, 2.12, 8.0, 0.6, 14)],
            [("增长由华东独撑，华北转为负增长", MX, 0.52, CW, 1.0, 26),
             ("正文", MX, 2.12, 8.0, 0.6, 14)],
            [("方案阶段流失最重", MX, 0.52, CW, 1.0, 26),
             ("正文", MX, 2.12, 8.0, 0.6, 14)],
        ])
        rep = audit.audit(path)
        self.assertTrue(any(r == "R15" for r, _, _ in rep.problems))

    def test_noun_title_is_flagged(self):
        path = deck([
            [("2024年度经营汇报", MX, 0.52, CW, 1.0, 26),
             ("正文", MX, 2.12, 8.0, 0.6, 14)],
            [("销售情况分析", MX, 0.52, CW, 1.0, 26),
             ("正文", MX, 2.12, 8.0, 0.6, 14)],
        ])
        rep = audit.audit(path)
        self.assertTrue(any(r == "R14" for r, _, _ in rep.problems))


class TestReport(unittest.TestCase):
    def test_blocking_counts_only_block_rules(self):
        path = deck([
            [TITLE, ("正文", MX, 2.12, 8.0, 0.6, 19)],
            [("结论", MX, 0.52, CW, 1.0, 26), ("正文", MX, 2.12, 8.0, 0.6, 14)],
        ])
        rep = audit.audit(path)
        block = [p for p in rep.problems if audit.R.by_id(p[0]).gate == "block"]
        self.assertEqual(rep.blocking, len(block))
        self.assertGreater(rep.blocking, 0)

    def test_json_roundtrip(self):
        path = deck([[TITLE, ("正文", MX, 2.12, 8.0, 0.6, 14)],
                     [("结论", MX, 0.52, CW, 1.0, 26), ("正文", MX, 2.12, 8.0, 0.6, 14)]])
        d = audit.audit(path).to_dict()
        self.assertEqual(d["deck"]["slides"], 2)
        self.assertEqual(len(d["slides"]), 2)
        self.assertIn("problems", d)


class TestLineLength(unittest.TestCase):
    """R05: a measured line, not a guessed one."""

    def test_wrapping_body_text_too_wide_is_flagged(self):
        long_line = "华东贡献了全年四分之三的增量，其余区域基本持平，其中华北已连续两个季度负增长" * 2
        path = deck([
            [TITLE, (long_line, 0.75, 2.12, 9.83, 1.5, 14)],
            [("结论", 0.75, 0.52, 11.83, 1.0, 26), ("正文", 0.75, 2.12, 4.0, 1.5, 14)],
        ])
        rep = audit.audit(path)
        self.assertTrue(any(r == "R05" for r, _, _ in rep.problems))

    def test_a_single_short_line_may_be_wide(self):
        # The rule is about the wrap width of prose, not about box width: a
        # one-line lead sentence is allowed to span the page.
        path = deck([
            [TITLE, ("一句话导语，不折行", 0.75, 2.12, 9.83, 0.5, 14)],
            [("结论", 0.75, 0.52, 11.83, 1.0, 26), ("正文", 0.75, 2.12, 4.0, 0.5, 14)],
        ])
        rep = audit.audit(path)
        self.assertFalse(any(r == "R05" for r, _, _ in rep.problems))


class TestFooterBaseline(unittest.TestCase):
    """R07: the bottom band is one line, not two."""

    def test_staggered_footer_is_flagged(self):
        path = deck([
            [TITLE,
             ("来源：经营分析会", 0.75, 7.05, 4.0, 0.3, 9),
             ("p.2", 11.5, 6.95, 1.0, 0.3, 9)],
            [("结论", 0.75, 0.52, 11.83, 1.0, 26), ("正文", 0.75, 2.12, 4.0, 0.5, 14)],
        ])
        rep = audit.audit(path)
        self.assertTrue(any(r == "R07" for r, _, _ in rep.problems))

    def test_aligned_footer_passes(self):
        path = deck([
            [TITLE,
             ("来源：经营分析会", 0.75, 7.05, 4.0, 0.3, 9),
             ("p.2", 11.5, 7.05, 1.0, 0.3, 9)],
            [("结论", 0.75, 0.52, 11.83, 1.0, 26), ("正文", 0.75, 2.12, 4.0, 0.5, 14)],
        ])
        rep = audit.audit(path)
        self.assertFalse(any(r == "R07" for r, _, _ in rep.problems))


class TestChartFontSource(unittest.TestCase):
    """R06 cannot be read out of a .pptx, so it is read out of the source."""

    def test_scanner_flags_below_floor(self):
        import tempfile as _t
        f = Path(_t.mkdtemp()) / "charts.py"
        f.write_text("ax.tick_params(labelsize=9)\nfig.text(..., fontsize=8.5)\n", encoding="utf-8")
        got = audit.chart_fonts(str(f))
        self.assertEqual([pt for pt, _ in got], [9.0, 8.5])

    def test_scanner_accepts_floor(self):
        import tempfile as _t
        f = Path(_t.mkdtemp()) / "charts.py"
        f.write_text("ax.tick_params(labelsize=11)\nfontsize=12\n", encoding="utf-8")
        self.assertEqual(audit.chart_fonts(str(f)), [])


class TestFractionalColumns(unittest.TestCase):
    """R02 fails in the file but is decided in the source."""

    def test_fractional_index_is_reported(self):
        import tempfile as _t
        f = Path(_t.mkdtemp()) / "render.py"
        f.write_text("add_text(s, colx(7.5), y, col(4.5), h)\n"
                     "add_text(s, colx(7), y, col(4), h)\n", encoding="utf-8")
        got = audit.fractional_columns(str(f))
        self.assertEqual([(c, i) for c, i, _ in got], [("colx", 7.5), ("col", 4.5)])

    def test_integer_columns_are_clean(self):
        import tempfile as _t
        f = Path(_t.mkdtemp()) / "render.py"
        f.write_text("add_text(s, colx(7), y, col(4), h)\n", encoding="utf-8")
        self.assertEqual(audit.fractional_columns(str(f)), [])


class TestPlanReplay(unittest.TestCase):
    """A saved plan must re-render: design changes are tested without the model."""

    def test_json_roundtrip(self):
        from deck.plan import Plan, Slide
        p = Plan("年度经营汇报", "给管理层", [
            Slide("title", "2024年度经营汇报"),
            Slide("chart", "华东拉动增长", fact="f1", bullets=["一条"]),
            Slide("closing", "下一步", bullets=["复制华东", "稳住东北"]),
        ])
        back = Plan.from_json(p.to_json())
        self.assertEqual(back.deck_title, p.deck_title)
        self.assertEqual([s.layout for s in back.slides], ["title", "chart", "closing"])
        self.assertEqual(back.slides[1].fact, "f1")
        self.assertEqual(back.slides[2].bullets, ["复制华东", "稳住东北"])


if __name__ == "__main__":
    unittest.main()

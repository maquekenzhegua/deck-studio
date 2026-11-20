"""Render + measure: the deck we ship has to pass the gate we publish.

These tests run the real renderer (no PowerPoint needed) and then the quality
checker, so a regression in either one shows up as a failed promise rather than
a bad screenshot noticed three days later.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deck import compose, compute, ingest, render2
from deck.visual import THEMES
from tools.qa import check


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="deck-render-"))
        cls.bundle = ingest.load(Path(__file__).resolve().parent.parent / "samples" / "data")
        cls.facts = compute.extract(cls.bundle)
        cls.plan = compose.draft(cls.bundle, cls.facts)

    def test_every_theme_builds(self) -> None:
        for name in THEMES:
            out = self.tmp / f"{name}.pptx"
            render2.build(self.plan, self.facts, self.bundle.pictures, out, theme=name)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 40_000, name)

    def test_quality_gate(self) -> None:
        out = self.tmp / "qa.pptx"
        render2.build(self.plan, self.facts, self.bundle.pictures, out, theme="editorial")
        report = check(out)
        self.assertEqual(report["pages"], len(self.plan.slides))
        self.assertGreaterEqual(report["score"], 90, report["issues"])
        self.assertFalse([i for i in report["issues"] if i["kind"] == "bounds"])

    def test_every_page_has_speaker_notes(self) -> None:
        from pptx import Presentation

        out = self.tmp / "notes.pptx"
        render2.build(self.plan, self.facts, self.bundle.pictures, out, theme="editorial")
        prs = Presentation(out)
        for i, slide in enumerate(prs.slides, 1):
            text = slide.notes_slide.notes_text_frame.text.strip()
            self.assertTrue(text, f"slide {i} has no notes")
        self.assertIn("依据", prs.slides[2].notes_slide.notes_text_frame.text)

    def test_ink_estimator_reads_a_known_page(self) -> None:
        from PIL import Image, ImageDraw

        from tools.qa import _ink

        page = self.tmp / "ink.png"
        im = Image.new("RGB", (400, 225), "#FBFAF6")
        ImageDraw.Draw(im).rectangle((20, 20, 380, 60), fill="#17140F")
        im.save(page)
        value, _ = _ink(page)
        self.assertGreater(value, 0.04)
        self.assertLess(value, 0.20)


if __name__ == "__main__":
    unittest.main()


DATA = Path(__file__).resolve().parent.parent / "samples" / "data"


class TwoMeasureTableTest(unittest.TestCase):
    """A comparison claim needs both numbers on the page."""

    @classmethod
    def setUpClass(cls) -> None:
        from deck import ingest, compute
        cls.bundle = ingest.load(DATA)
        cls.facts = compute.extract(cls.bundle)

    def test_second_fact_becomes_a_column(self):
        import tempfile
        from pathlib import Path
        from deck.plan import Plan, Slide
        from pptx import Presentation
        money = next(f for f in self.facts if f.unit == "money" and len(f.points) >= 4)
        count = next(f for f in self.facts if f.unit == "count" and len(f.points) >= 4
                     and {k for k, _ in f.points} == {k for k, _ in money.points})
        plan = Plan(deck_title="d", subtitle="", slides=[
            Slide(layout="table", title="两边一起看", fact=f"{money.id},{count.id}"),
            Slide(layout="closing", title="收尾", bullets=["一", "二"]),
        ])
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "d.pptx"
            from deck import render2
            render2.build(plan, self.facts, {}, out, theme="editorial")
            prs = Presentation(str(out))
            text = "\n".join(sh.text_frame.text for sl in prs.slides
                             for sh in sl.shapes if sh.has_text_frame)
        vhead = (money.column or money.label).split("按")[0].strip()[:6]
        self.assertIn(vhead, text)
        head = (count.column or count.label).split("按")[0].strip()[:6]
        self.assertIn(head, text)

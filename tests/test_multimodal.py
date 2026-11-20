"""Multimodal intake: what the tool can tell about a file without a vision model.

The claims here are deliberately modest — kind, palette, page count — because
those are the things we can measure. Anything stronger would be a promise the
offline path cannot keep.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deck import ingest, multimodal

DATA = Path(__file__).resolve().parent.parent / "samples" / "data"


class AnalyseTest(unittest.TestCase):
    def test_chart_png_is_a_chart(self) -> None:
        info = multimodal.analyse(DATA / "产品线结构.png")
        self.assertEqual(info["kind"], "chart")
        self.assertGreater(info["white"], 0.4)
        self.assertTrue(info["palette"])

    def test_bare_axes_png_is_a_chart(self) -> None:
        info = multimodal.analyse(DATA / "区域分布示意.png")
        self.assertEqual(info["kind"], "chart")

    def test_photo_is_a_photo(self) -> None:
        photo = Path(__file__).resolve().parent.parent / "samples" / "photos" / "hero.jpg"
        if not photo.exists():
            self.skipTest("no sample photo")
        info = multimodal.analyse(photo)
        self.assertEqual(info["kind"], "photo")

    def test_pdf_pages_become_pictures(self) -> None:
        pics: list = []
        added = multimodal.pdf_pages(DATA / "行业简报.pdf", pics,
                                     Path(tempfile.mkdtemp(prefix="deck-test-")))
        self.assertGreater(added, 0)
        self.assertEqual(pics[0].kind, "page")
        self.assertIn("第 1 页", pics[0].caption)


class BundleTest(unittest.TestCase):
    def test_augment_marks_every_picture(self) -> None:
        bundle = ingest.load(DATA)
        result = multimodal.augment(bundle, Path("."), pages=False)
        self.assertEqual(result, bundle)
        for p in bundle.pictures:
            self.assertTrue(p.kind, p.name)
            self.assertTrue(p.caption, p.name)

    def test_caption_has_no_stray_colour(self) -> None:
        bundle = ingest.load(DATA)
        multimodal.augment(bundle, Path("."), pages=False)
        for p in bundle.pictures:
            self.assertNotIn("主色白", p.caption)
            self.assertIn(" · ", p.caption)


if __name__ == "__main__":
    unittest.main()


class ChartInsideDeckTest(unittest.TestCase):
    """A previous deck is often where the numbers live."""

    def test_chart_values_become_facts(self):
        import tempfile
        from pptx import Presentation
        from pptx.chart.data import CategoryChartData
        from pptx.enum.chart import XL_CHART_TYPE
        from pptx.util import Inches
        from deck import compute, multimodal

        tmp = Path(tempfile.mkdtemp(prefix="deck-chart-"))
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        data = CategoryChartData()
        data.categories = ["华东", "华南", "华北"]
        data.add_series("销售额", (977.2, 653.7, 508.5))
        slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,
                               Inches(1), Inches(1), Inches(6), Inches(4), data)
        deck = tmp / "去年.pptx"
        prs.save(deck)

        charts = multimodal.pptx_charts(deck)
        self.assertEqual(len(charts), 1)
        self.assertEqual(charts[0]["cats"], ["华东", "华南", "华北"])
        self.assertAlmostEqual(charts[0]["vals"][0], 977.2, places=1)

        bundle = ingest.load(tmp)
        multimodal.augment(bundle, tmp / "media")
        self.assertTrue(any("图表" in t.name for t in bundle.tables))
        points = [p for f in compute.extract(bundle) for p in f.points]
        self.assertIn(("华东", 977.2), points)

"""The offline planner has to produce a deck that survives its own audit.

No model, no network: these tests encode the promises `compose.py` makes when
there is nobody to write the prose — every title is a claim, every number comes
from a fact, and the page order tells a story instead of listing tables.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from deck import compose, compute, ingest, render2, verify

DATA = Path(__file__).resolve().parent.parent / "samples" / "data"


class ComposeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = ingest.load(DATA)
        cls.facts = compute.extract(cls.bundle)
        cls.by_id = {f.id: f for f in cls.facts}
        cls.plan = compose.draft(cls.bundle, cls.facts)

    def test_page_order(self) -> None:
        layouts = [s.layout for s in self.plan.slides]
        self.assertEqual(layouts[0], "title")
        self.assertEqual(layouts[-1], "closing")
        self.assertIn("section", layouts)
        self.assertLessEqual(len(layouts), 15)

    def test_chart_variety(self) -> None:
        """A deck of five bar charts is a spreadsheet with margins."""
        variants = {s.variant for s in self.plan.slides if s.layout == "chart"}
        kinds = {s.layout for s in self.plan.slides}
        self.assertIn("multi", variants)
        self.assertIn("donut", variants)
        self.assertIn("heatmap", variants)
        self.assertIn("waterfall", variants)
        self.assertTrue({"chart", "table", "compare"} <= kinds)
        self.assertGreaterEqual(len(kinds), 7, sorted(kinds))

    def test_heatmap_pages_line_up(self) -> None:
        """Every column of a scorecard must describe the same rows."""
        for s in self.plan.slides:
            if s.variant != "heatmap":
                continue
            ids = [x for x in (s.fact or "").split(",") if x]
            self.assertGreaterEqual(len(ids), 2)
            cats = [{k for k, _ in self.by_id[i].points} for i in ids]
            self.assertEqual(len({frozenset(c) for c in cats}), 1)

    def test_titles_are_claims(self) -> None:
        for s in self.plan.slides:
            self.assertTrue(s.title.strip(), f"{s.layout} has an empty title")
            if s.layout == "quote":
                # the quote is the client's own sentence, not our headline
                self.assertTrue(s.note.startswith("——"), s.title)
                self.assertLessEqual(len(s.title), 58, s.title)
                continue
            self.assertLessEqual(len(s.title), 34, s.title)
            # a section/cover label such as "销售情况分析" is not a claim
            self.assertFalse(s.title.endswith("分析"), s.title)

    def test_quote_comes_from_a_document(self) -> None:
        quotes = [s for s in self.plan.slides if s.layout == "quote"]
        self.assertEqual(len(quotes), 1)
        q = quotes[0]
        # Which document wins is a matter of order and length; what matters is
        # that the page names the file it came from and does not invent one.
        self.assertTrue(q.note.startswith("—— "), q.note)
        self.assertGreater(len(q.note.strip("—— ")), 2)
        self.assertNotRegex(q.title, r"\d")

    def test_every_fact_reference_exists(self) -> None:
        for s in self.plan.slides:
            for fid in re.findall(r"\{\{(f\d+)\}\}", s.title + s.note):
                self.assertIn(fid, self.by_id)

    def test_number_gate_passes(self) -> None:
        problems = verify.check(self.plan, self.facts)
        self.assertEqual(problems, [])

    def test_funnel_names_are_stages_not_values(self) -> None:
        text = " ".join(s.title for s in self.plan.slides)
        self.assertNotRegex(text, r"到\d")
        if "商机" in text:
            self.assertIn("商机到方案", text)

    def test_action_page_carries_evidence(self) -> None:
        actions = [s for s in self.plan.slides if s.layout == "bullets"]
        self.assertTrue(actions)
        bodies = actions[0].bullets
        self.assertEqual(len(bodies), 3)
        self.assertTrue(any("%" in b for b in bodies), bodies)

    def test_source_material_page_when_pictures_exist(self) -> None:
        layouts = [s.layout for s in self.plan.slides]
        if self.bundle.pictures:
            self.assertTrue({"split_image", "image_full"} & set(layouts))


if __name__ == "__main__":
    unittest.main()


class TextOnlyTest(unittest.TestCase):
    """A deck made of prose still has to be a deck, not an apology."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        cls.tmp = Path(tempfile.mkdtemp(prefix="deck-text-"))
        (cls.tmp / "说明.md").write_text(
            "# 经营小结\n\n项目周报本周完成了渠道复盘。下周重点是把华东的打法复制到西南。\n"
            "整体节奏比预期慢，主要卡在供应链。\n", encoding="utf-8")
        cls.bundle = ingest.load(cls.tmp)
        cls.plan = compose.draft_text(cls.bundle)

    def test_pages(self) -> None:
        layouts = [s.layout for s in self.plan.slides]
        self.assertEqual(layouts[0], "title")
        self.assertEqual(layouts[-1], "closing")
        self.assertIn("quote", layouts)

    def test_no_invented_numbers(self) -> None:
        for s in self.plan.slides:
            self.assertIsNone(re.search(r"\d", s.title), s.title)

    def test_extracts_cite_source(self) -> None:
        for s in self.plan.slides:
            if s.layout != "notes":
                continue
            self.assertTrue(s.bullets)
            # The sentence stays whole; where it came from is a page-level
            # footnote, not a suffix glued to the quotation.
            self.assertIn("来源：", s.note)
            self.assertTrue(s.note.split("来源：")[-1].strip())
            for b in s.bullets:
                self.assertNotIn("来源", b)

    def test_renders(self) -> None:
        from deck import render2
        out = self.tmp / "text.pptx"
        render2.build(self.plan, {}, {}, out, theme="editorial")
        self.assertTrue(out.exists() and out.stat().st_size > 20000)

    def test_compare_page_puts_growth_and_decline_against_each_other(self):
        b = ingest.load(DATA)
        p = compose.draft(b, compute.extract(b))
        cmp_slides = [s for s in p.slides if s.layout == "compare"]
        self.assertTrue(cmp_slides, "mixed growth should earn a compare page")
        s = cmp_slides[0]
        self.assertEqual(len(s.bullets), 2)
        left, right = s.bullets
        self.assertIn("还在涨", left)
        self.assertIn("在退", right)
        # the right column is the losing side: every number there is negative
        self.assertIn("-", right)
        self.assertNotIn("+", right)
        self.assertEqual(verify.check(p, compute.extract(b)), [])

class PictureOnlyTest(unittest.TestCase):
    """Five photos and no spreadsheet is still a deck, just not a chart deck."""

    def _bundle(self):
        from PIL import Image

        from deck.ingest import Bundle, Picture

        tmp = Path(tempfile.mkdtemp(prefix="deck-pics-"))
        pics = []
        for i, colour in enumerate(((200, 80, 60), (40, 90, 140)), 1):
            path = tmp / f"现场{i}.jpg"
            Image.new("RGB", (1200, 800), colour).save(path)
            pics.append(Picture(f"p{i}", f"现场{i}.jpg", str(path), 1200, 800, ""))
        return Bundle(tables=[], docs=[], pictures=pics, outlines=[], skipped=[])

    def test_a_photo_folder_becomes_a_deck(self):
        bundle = self._bundle()
        plan = compose.draft_pictures(bundle)
        layouts = [s.layout for s in plan.slides]
        self.assertEqual(layouts[0], "title")
        self.assertEqual(layouts[-1], "closing")
        # The cover carries the first picture, so the body starts at the second.
        self.assertEqual(layouts.count("image_full"), 1)
        self.assertNotIn("chart", layouts)
        self.assertNotIn("kpi_row", layouts)

    def test_a_photo_deck_can_be_rendered(self):
        bundle = self._bundle()
        plan = compose.draft_pictures(bundle)
        plan.deck_title = plan.deck_title  # subtitle is what verify reads
        self.assertEqual(verify.check(plan, []), [])
        out = Path(tempfile.mkdtemp()) / "pics.pptx"
        render2.build(plan, [], bundle.pictures, str(out))
        self.assertGreater(out.stat().st_size, 10_000)

class SectionPhotoTest(unittest.TestCase):
    """A chapter page borrows the cover's subject, not a blank gradient."""

    def test_section_page_carries_a_photo(self):
        import tempfile, pathlib
        from pptx import Presentation
        from deck import compute, ingest, multimodal, compose, render2
        b = ingest.load(DATA)
        multimodal.augment(b, pathlib.Path(tempfile.mkdtemp()) / "m")
        facts = compute.extract(b)
        plan = compose.draft(b, facts)
        out = pathlib.Path(tempfile.mkdtemp()) / "s.pptx"
        render2.build(plan, facts, b.pictures, out, theme="editorial")
        prs = Presentation(str(out))
        page = prs.slides[1]
        pics = [sh for sh in page.shapes if sh.shape_type == 13]
        self.assertTrue(pics, "section page should carry a picture when the material has a photo")

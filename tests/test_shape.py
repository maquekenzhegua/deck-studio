"""A deck has to survive data it was not tuned on.

The sample workbook has regions, a funnel and a product mix. A real workbook
might have channels and quarters, and the first version of the composer told
that reader "区域分化明显" over a channel table and turned a ranking into a
funnel because the numbers happened to fall.
"""
import unittest

import pandas as pd

from deck import compose, compute, ingest, verify
from deck.ingest import Bundle, Table


def _bundle() -> Bundle:
    rows = []
    for q, mult in (("Q1", 1.0), ("Q2", 1.15), ("Q3", 1.3), ("Q4", 1.45)):
        for ch, base in (("直营", 120), ("经销", 95), ("线上", 60), ("海外", 25)):
            rows.append({"季度": q, "渠道": ch,
                         "收入": round(base * mult, 1),
                         "转化率": round(0.21 - 0.01 * ("直营 经销 线上 海外".split().index(ch)), 3)})
    df = pd.DataFrame(rows)
    return Bundle(tables=[Table("渠道季度", df, "渠道季度.xlsx", "Sheet1")],
                  docs=[], pictures=[], outlines=[], skipped=[])


class ShapeTest(unittest.TestCase):
    def setUp(self):
        self.bundle = _bundle()
        self.facts = compute.extract(self.bundle)

    def test_both_dimensions_become_facts(self):
        labels = " ".join(f.label for f in self.facts)
        self.assertIn("按 季度", labels)
        self.assertIn("按 渠道", labels)

    def test_a_channel_ranking_is_not_a_funnel(self):
        by_channel = [f for f in self.facts if "按 渠道" in f.label and f.unit == "money"]
        self.assertTrue(by_channel)
        self.assertTrue(all(f.chart == "bar" for f in by_channel))

    def test_the_subtitle_names_the_metric_not_a_guess(self):
        plan = compose.draft(self.bundle, self.facts)
        self.assertNotIn("区域", plan.subtitle)

    def test_a_thin_dataset_still_gets_two_moves(self):
        plan = compose.draft(self.bundle, self.facts)
        actions = [s for s in plan.slides if s.layout == "bullets"]
        self.assertTrue(actions)
        self.assertGreaterEqual(len(actions[0].bullets), 2)

    def test_no_three_identical_layouts_in_a_row(self):
        plan = compose.draft(self.bundle, self.facts)
        layouts = [s.layout for s in plan.slides]
        for i in range(2, len(layouts)):
            self.assertFalse(layouts[i] == layouts[i - 1] == layouts[i - 2],
                             f"slides {i - 2}-{i} are all {layouts[i]}")


class ThinTest(unittest.TestCase):
    """One column of numbers is not a deck; say so instead of pretending."""

    def setUp(self):
        import tempfile
        from pathlib import Path as P
        import pandas as pd
        self.tmp = tempfile.TemporaryDirectory()
        root = P(self.tmp.name)
        pd.DataFrame({"金额": [12.4, 18.9, 7.2]}).to_excel(root / "单列.xlsx", index=False)
        self.bundle = ingest.load(root)
        self.facts = compute.extract(self.bundle)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_facts_gets_an_honest_page_not_an_empty_deck(self):
        self.assertEqual(self.facts, [])
        plan = compose.draft(self.bundle, self.facts)
        self.assertLessEqual(len(plan.slides), 4)
        self.assertIn("讲不出结论", plan.deck_title)
        self.assertIn("补", " ".join(plan.slides[1].bullets))


class FunnelWithoutConversionTest(unittest.TestCase):
    """Stage names and a steep drop are enough; a conversion column is optional."""

    def setUp(self):
        import tempfile
        from pathlib import Path as P
        import pandas as pd
        self.tmp = tempfile.TemporaryDirectory()
        root = P(self.tmp.name)
        pd.DataFrame({"阶段": ["曝光", "点击", "加购", "下单", "支付"],
                      "人数": [120000, 24000, 6100, 1800, 640]}).to_excel(root / "转化.xlsx", index=False)
        self.bundle = ingest.load(root)
        self.facts = compute.extract(self.bundle)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stage_shape_is_recognised_as_a_process(self):
        self.assertTrue(any(f.chart == "funnel" for f in self.facts))
        plan = compose.draft(self.bundle, self.facts)
        self.assertIn("table", [s.layout for s in plan.slides])
        self.assertEqual(verify.check(plan, self.facts), [])

    def test_the_title_of_the_action_page_matches_what_is_on_it(self):
        plan = compose.draft(self.bundle, self.facts)
        for s in plan.slides:
            if s.layout == "bullets":
                n = len(s.bullets)
                self.assertNotIn("三件", s.title if n < 3 else "")


if __name__ == "__main__":
    unittest.main()

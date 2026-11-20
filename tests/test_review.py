import unittest
from pathlib import Path

from deck import compose, compute, ingest, review
from deck.plan import Plan, Slide

DATA = Path(__file__).resolve().parent.parent / "samples" / "data"


class ReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = ingest.load(DATA)
        cls.facts = compute.extract(cls.bundle)

    def test_the_generated_deck_passes(self):
        plan = compose.draft(self.bundle, self.facts)
        found = review.review(plan, self.facts)
        blocks = [f for f in found if f.level == "block"]
        self.assertEqual(blocks, [], [f.text for f in blocks])
        self.assertEqual(review.summary([]), "没有发现问题")

    def test_a_number_only_title_is_blocked(self):
        plan = Plan(deck_title="t", subtitle="", slides=[Slide(layout="chart", title="977.2", fact="f13")])
        found = review.review(plan, self.facts)
        self.assertTrue(any(f.level == "block" and "不是一句判断" in f.text for f in found))

    def test_a_chart_without_a_fact_is_blocked(self):
        plan = Plan(deck_title="t", subtitle="", slides=[Slide(layout="chart", title="销售额在涨")])
        found = review.review(plan, self.facts)
        self.assertTrue(any(f.level == "block" and "没有绑定" in f.text for f in found))

    def test_three_charts_in_a_row_are_flagged(self):
        plan = Plan(deck_title="t", subtitle="", slides=[
            Slide(layout="title", title="封面"),
            Slide(layout="chart", title="销售额在涨", fact="f1"),
            Slide(layout="chart", title="订单在涨", fact="f8"),
            Slide(layout="chart", title="客户在涨", fact="f18"),
            Slide(layout="closing", title="结束"),
        ])
        found = review.review(plan, self.facts)
        self.assertTrue(any("连续 3 页" in f.text for f in found))

    def test_a_long_quote_is_not_a_long_title(self):
        long_line = "智能硬件仍是最大收入来源，但增速在下半年明显放缓，主要原因是渠道库存调整。"
        plan = Plan(deck_title="t", subtitle="", slides=[
            Slide(layout="quote", title=long_line, note="—— 经营小结")])
        found = review.review(plan, self.facts)
        self.assertFalse(any("超过" in f.text for f in found))

    def test_prompt_lists_the_pages(self):
        found = [review.Finding("warn", 3, "标题 30 字，超过 26 字。")]
        text = review.as_prompt(found)
        self.assertIn("第 3 页", text)
        self.assertTrue(text.splitlines()[0].startswith("按这些评审意见"))


if __name__ == "__main__":
    unittest.main()


class FactReuseTest(unittest.TestCase):
    """One fact can carry a page; it cannot carry the deck."""

    def test_three_pages_on_one_fact_raise_a_warning(self):
        from deck.plan import Plan, Slide
        p = Plan(deck_title="d", subtitle="", slides=[
            Slide(layout="chart", title="一", fact="f1"),
            Slide(layout="kpi_row", title="二", fact="f1"),
            Slide(layout="table", title="三", fact="f1"),
        ])
        findings = review.review(p, [])
        self.assertTrue(any("f1" in f.text and f.level == "warn" for f in findings))

    def test_two_pages_on_one_fact_are_fine(self):
        from deck.plan import Plan, Slide
        p = Plan(deck_title="d", subtitle="", slides=[
            Slide(layout="chart", title="一", fact="f1"),
            Slide(layout="chart", title="二", fact="f2"),
            Slide(layout="table", title="三", fact="f1"),
        ])
        findings = review.review(p, [])
        self.assertFalse(any("已经出现在" in f.text for f in findings))

"""A title that claims a shape has to be drawn in that shape.

The failure this guards against is quiet: six `chart` slides in a row, all
bars, while three of the titles talk about shares, deltas and divergence. The
deck is not wrong, it just never shows the thing it is arguing about.
"""

from __future__ import annotations

import unittest

from deck import chartpick
from deck.compute import Fact
from deck.plan import Plan, Slide

MONEY = [
    ("智能硬件", 1278.3), ("企业软件", 827.7), ("运维服务", 396.1),
]


def fact(**kw) -> Fact:
    base = dict(id="f1", kind="money", label="销售额 按 产品线", value="2502.1 万",
                raw=2502.1, source="销售数据.xlsx", column="销售额",
                unit="money", agg="sum", chart="bar", points=MONEY, score=2.0)
    base.update(kw)
    return Fact(**base)


class ChooseTest(unittest.TestCase):
    def test_a_share_claim_gets_a_ring(self) -> None:
        self.assertEqual(chartpick.choose(fact(), "智能硬件吃掉一半收入"), "donut")

    def test_two_parts_are_not_a_ring(self) -> None:
        two = fact(points=[("甲", 60.0), ("乙", 40.0)])
        self.assertEqual(chartpick.choose(two, "两块业务各占一半"), "")

    def test_a_contribution_claim_gets_a_bridge(self) -> None:
        self.assertEqual(chartpick.choose(fact(), "增量几乎全部来自华东"), "waterfall")

    def test_a_rate_cannot_be_a_bridge(self) -> None:
        rate = fact(unit="ratio", points=[("华东", 0.34), ("华南", 0.11), ("东北", -0.12)])
        self.assertEqual(chartpick.choose(rate, "增量来自华东"), "")

    def test_divergence_gets_small_multiples(self) -> None:
        series = {"智能硬件": [("1月", 1.0), ("2月", 2.0)], "运维服务": [("1月", 1.0), ("2月", 1.1)]}
        multi = fact(chart="multiline", series=series)
        self.assertEqual(chartpick.choose(multi, "智能硬件在加速，运维没有跟上"), "multi")

    def test_an_ordinary_claim_is_left_alone(self) -> None:
        self.assertEqual(chartpick.choose(fact(), "华东规模第一"), "")


class ApplyTest(unittest.TestCase):
    def plan_with(self, title: str, variant: str = "", fact_ids: str = "f1") -> Plan:
        return Plan("标题", "副标", [Slide(layout="chart", title=title, fact=fact_ids, variant=variant)])

    def test_it_rewrites_the_variant_and_says_which_page(self) -> None:
        plan, log = chartpick.apply(self.plan_with("智能硬件吃掉一半收入"), [fact()])
        self.assertEqual(plan.slides[0].variant, "donut")
        self.assertTrue(any("第 1 页" in line for line in log))

    def test_a_bridge_needs_two_facts_and_is_skipped_without_them(self) -> None:
        plan, log = chartpick.apply(self.plan_with("增量几乎全部来自华东"), [fact()])
        self.assertEqual(plan.slides[0].variant, "")
        self.assertEqual(log, [])

    def test_both_facts_allow_the_bridge(self) -> None:
        growth = fact(id="f2", unit="ratio", kind="ratio", points=[("华东", 0.34), ("华南", 0.11), ("东北", -0.12)])
        plan, _ = chartpick.apply(self.plan_with("增量几乎全部来自华东", fact_ids="f1,f2"), [fact(), growth])
        self.assertEqual(plan.slides[0].variant, "waterfall")

    def test_a_scorecard_is_left_alone(self) -> None:
        plan = self.plan_with("智能硬件吃掉一半收入", variant="heatmap", fact_ids="f1,f2")
        chartpick.apply(plan, [fact()])
        self.assertEqual(plan.slides[0].variant, "heatmap")

    def test_other_layouts_are_never_touched(self) -> None:
        plan = Plan("标题", "副标", [Slide(layout="table", title="智能硬件吃掉一半收入", fact="f1")])
        chartpick.apply(plan, [fact()])
        self.assertEqual(plan.slides[0].variant, "")


if __name__ == "__main__":
    unittest.main()

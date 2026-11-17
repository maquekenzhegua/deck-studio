"""The three promises this tool makes, as tests.

Run with:  python -m unittest discover -s tests -v
No API key needed — everything here is offline.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from deck import compute, ingest, plan, verify  # noqa: E402
from deck.compute import Fact  # noqa: E402
from deck.plan import Plan, Slide  # noqa: E402
from deck.render import subst  # noqa: E402


def make_fact(**kw) -> Fact:
    base = dict(
        id="f1", kind="scalar", label="销售额", value="2,502.1 万",
        raw=25021000.0, source="销售数据.xlsx / 月度销售", column="销售额",
        unit="money", agg="sum", chart=None, points=[], change=None, score=1.0,
        number="2,502.1 万", top=None,
    )
    base.update(kw)
    return Fact(**base)


def make_bundle(**tables) -> ingest.Bundle:
    return ingest.Bundle(
        tables=[
            ingest.Table(name=n, df=df, source=f"{n}.xlsx", sheet=n)
            for n, df in tables.items()
        ],
        docs=[], pictures=[], skipped=[],
    )


class PromiseOneNumbersAreComputed(unittest.TestCase):
    """Promise 2: the model never does arithmetic."""

    def test_rate_column_is_averaged_not_summed(self) -> None:
        self.assertEqual(compute._classify("同比增长"), ("ratio", "mean"))
        self.assertEqual(compute._classify("转化率"), ("ratio", "mean"))
        self.assertEqual(compute._classify("占比"), ("ratio", "mean"))

    def test_a_rate_never_renders_above_100_percent(self) -> None:
        df = pd.DataFrame({"区域": ["华东", "华南", "华北"], "同比增长": [0.34, 0.12, -0.08]})
        facts = compute.extract(make_bundle(区域=df))
        rates = [f for f in facts if f.unit == "ratio"]
        self.assertTrue(rates, "no ratio fact produced")
        for f in rates:
            self.assertIsInstance(f.raw, float)
            self.assertLessEqual(abs(f.raw), 1.0, f"{f.id} {f.label} raw={f.raw}")
            # 0.34 must print as "34.0%", never as "0.34".
            self.assertIn("%", f.number or f.value)

    def test_count_and_money_still_sum(self) -> None:
        self.assertEqual(compute._classify("销售额"), ("money", "sum"))
        self.assertEqual(compute._classify("订单数"), ("count", "sum"))

    def test_averaged_money_keeps_its_currency(self) -> None:
        self.assertEqual(compute._classify("客单价"), ("money", "mean"))
        self.assertEqual(compute._classify("平均停留天数"), ("raw", "mean"))

    def test_scalar_mean_is_labelled(self) -> None:
        df = pd.DataFrame({"客单价": [5000, 5200, 4900]})
        scalars = [f for f in compute.extract(make_bundle(t=df)) if f.kind == "scalar"]
        self.assertTrue(scalars)
        self.assertIn("均值", scalars[0].label)


class PromiseTwoNumbersHaveASource(unittest.TestCase):
    """Promise 1: every number on a slide traces back to a cell."""

    def test_fabricated_number_is_caught(self) -> None:
        facts = [make_fact()]
        p = Plan(deck_title="T", subtitle="", slides=[
            Slide(layout="bullets", title="增长来自华东", fact=None, image=None,
                  note=None, bullets=["华东贡献了 37% 的增量"]),
        ])
        problems = verify.check(p, facts)
        self.assertTrue(any(pr.kind == "number" for pr in problems), problems)

    def test_real_number_passes(self) -> None:
        facts = [make_fact()]
        p = Plan(deck_title="T", subtitle="", slides=[
            Slide(layout="bullets", title="全年销售额", fact=None, image=None,
                  note=None, bullets=["全年销售额 {{f1}}"]),
        ])
        self.assertEqual([str(pr) for pr in verify.check(p, facts)], [])

    def test_chart_page_without_a_fact_is_caught(self) -> None:
        p = Plan(deck_title="T", subtitle="", slides=[
            Slide(layout="chart", title="趋势", fact=None, image=None,
                  note=None, bullets=[]),
        ])
        problems = verify.check(p, [])
        self.assertTrue(any(pr.kind == "structure" for pr in problems), problems)

    def test_an_image_page_without_an_image_is_caught(self) -> None:
        p = Plan(deck_title="T", subtitle="", slides=[
            Slide(layout="split_image", title="结构", fact=None, image=None,
                  note=None, bullets=[]),
        ])
        problems = verify.check(p, [])
        self.assertTrue(any(pr.kind == "structure" for pr in problems), problems)


class PromiseThreeTheModelOnlyNarrates(unittest.TestCase):
    def test_echoing_the_request_is_dropped(self) -> None:
        ask = "给管理层看的2024年度经营汇报，重点讲增长来自哪里、问题在哪"
        self.assertTrue(plan._echo(ask, ask))
        self.assertTrue(plan._echo("2024年度经营汇报", ask))
        self.assertFalse(plan._echo("增长靠华东，风险在东北与转化率", ask))

    def test_placeholder_is_substituted_with_the_fact_value(self) -> None:
        facts = {"f3": make_fact(id="f3", value="华东 34.0%", number="34.0%")}
        self.assertEqual(subst("增速领跑的是 {{f3}}", facts), "增速领跑的是 华东 34.0%")

    def test_unknown_placeholder_is_left_visible_not_blanked(self) -> None:
        # A missing fact must show up as {{f9}} on the slide, because a silent
        # empty string is a lie nobody can see.
        self.assertEqual(subst("增速 {{f9}}", {}), "增速 {{f9}}")


class Ingest(unittest.TestCase):
    def test_sample_directory_loads(self) -> None:
        root = Path(__file__).resolve().parents[1] / "samples" / "data"
        if not root.exists():
            self.skipTest("samples/data not generated; run python samples/make_sample.py")
        bundle = ingest.load(root)
        self.assertGreaterEqual(len(bundle.tables), 2)
        self.assertGreaterEqual(len(bundle.pictures), 1)
        self.assertEqual(bundle.skipped, [])

    def test_broken_file_is_skipped_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            Path(td, "ok.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            Path(td, "bad.xlsx").write_bytes(b"not a workbook")
            bundle = ingest.load(Path(td))
            self.assertEqual(len(bundle.tables), 1)
            self.assertEqual(len(bundle.skipped), 1)


if __name__ == "__main__":
    unittest.main()

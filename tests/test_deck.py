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


class TestNewFormats(unittest.TestCase):
    """PDF and pptx: the two formats that actually show up in a work folder."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "samples" / "data"
        if not (root / "去年经营汇报.pptx").exists():
            raise unittest.SkipTest("samples/data not generated; run python samples/make_sample.py")
        cls.b = ingest.load(root)

    def test_pdf_text_is_readable_not_mojibake(self):
        pdf = [d for d in self.b.docs if d.name.endswith(".pdf")]
        self.assertTrue(pdf, "样例 PDF 没被读到")
        self.assertIn("行业简报", pdf[0].text)
        self.assertIn("9.1%", pdf[0].text)

    def test_deck_outline_keeps_page_order(self):
        self.assertTrue(self.b.outlines, "样例 pptx 没被读到")
        o = self.b.outlines[0]
        self.assertEqual(len(o.pages), 5)
        self.assertIn("经营汇报", o.pages[0])
        self.assertIn("明年三件事", o.pages[-1])

    def test_outline_block_reaches_the_prompt(self):
        block = self.b.outline_block()
        self.assertIn("已有的 deck 结构", block)
        self.assertIn("01", block)

    def test_deck_text_becomes_a_doc(self):
        names = [d.name for d in self.b.docs]
        self.assertIn("去年经营汇报.pptx", names)

    def test_unknown_extension_is_skipped_not_fatal(self):
        import tempfile, pathlib as pl
        with tempfile.TemporaryDirectory() as td:
            pl.Path(td, "note.xyz").write_text("x", encoding="utf-8")
            pl.Path(td, "a.csv").write_text("区域,销售额\n华东,10\n华南,8\n", encoding="utf-8")
            b = ingest.load(td)
        self.assertEqual(len(b.tables), 1)
        self.assertTrue(any("note.xyz" in s for s in b.skipped))


class TestQuantifiers(unittest.TestCase):
    """A deck can lie with every number correct. "华北是唯一下滑的大区"
    shipped on a page whose own chart showed -4.0% and -12.0%; both numbers
    traced to a fact, so the number check passed it."""

    def _fact(self, **kw):
        base = dict(
            id="f1", kind="breakdown", label="同比", value="华东 34.0%",
            raw=0.34, source="x.xlsx / 区域", column="同比", unit="ratio",
            agg="mean", chart="bar", points=[("华东", 0.34), ("东北", -0.12),
                                            ("华北", -0.04)],
        )
        base.update(kw)
        return Fact(**base)

    def _plan(self, title, fid="f1"):
        from deck.plan import Plan, Slide
        return Plan("t", "", [Slide("chart", title, fid, "", "", ["a"])])

    def test_exclusive_decline_with_two_negatives_is_caught(self):
        probs = verify.check(self._plan("华东领涨，华北是唯一下滑的大区"),
                             [self._fact()])
        self.assertTrue(any(p.kind == "number" and "唯一" in p.detail for p in probs))

    def test_exclusive_decline_with_one_negative_passes(self):
        f = self._fact(points=[("华东", 0.34), ("华北", -0.04), ("华南", 0.11)])
        probs = verify.check(self._plan("华东领涨，华北是唯一下滑的大区"), [f])
        self.assertFalse(any(p.kind == "number" for p in probs))

    def test_all_up_claim_with_a_declining_point_is_caught(self):
        probs = verify.check(self._plan("六大区全部增长"), [self._fact()])
        self.assertTrue(any("全部" in p.detail for p in probs))

    def test_scalar_fact_is_not_tested_for_quantifiers(self):
        f = self._fact(kind="scalar", points=[])
        probs = verify.check(self._plan("华东是唯一正增长的大区"), [f])
        self.assertFalse(any(p.kind == "number" for p in probs))


if __name__ == "__main__":
    unittest.main()

class DuplicateFactTest(unittest.TestCase):
    """The same numbers twice is one claim, not two pages."""

    def test_identical_tables_do_not_double_the_facts(self) -> None:
        import pandas as pd

        from deck import compute
        from deck.ingest import Bundle, Table

        frame = pd.DataFrame({"区域": ["华东", "华南", "华北"], "销售额": [977, 654, 509]})
        bundle = Bundle(
            tables=[Table("主表", frame, "a.xlsx", "Sheet1"),
                    Table("附表", frame.copy(), "b.xlsx", "Sheet1")],
            docs=[], pictures=[], outlines=[], skipped=[],
        )
        facts = compute.extract(bundle)
        labels = [f.label for f in facts]
        self.assertEqual(labels.count("销售额 按 区域"), 1, labels)
        self.assertEqual(labels.count("销售额"), 1, labels)


if __name__ == "__main__":
    unittest.main()

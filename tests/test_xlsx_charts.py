"""Charts authored inside a workbook are data too.

Excel stores a cached copy of every series in the chart part; openpyxl (and
several exporters) write only the cell reference. Both paths have to end in
the same place: a table the numbers can be counted from.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference

from deck import compute, ingest, multimodal


def _book(folder: Path, with_chart: bool = True) -> Path:
    path = folder / "区域销售.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(["区域", "销售额"])
    for row in (("华东", 977), ("华南", 654), ("华北", 509)):
        ws.append(list(row))
    if with_chart:
        chart = BarChart()
        chart.title = "区域销售额"
        chart.add_data(
            Reference(ws, min_col=2, min_row=1, max_row=4), titles_from_data=True
        )
        chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=4))
        ws.add_chart(chart, "D2")
    wb.save(path)
    return path


class WorksheetChartTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_reads_values_when_the_cache_is_empty(self) -> None:
        charts = multimodal.xlsx_charts(_book(self.tmp))
        self.assertEqual(len(charts), 1)
        self.assertEqual(charts[0]["vals"], [977.0, 654.0, 509.0])
        self.assertEqual(charts[0]["cats"], ["华东", "华南", "华北"])

    def test_series_name_comes_from_the_header_cell(self) -> None:
        charts = multimodal.xlsx_charts(_book(self.tmp))
        self.assertEqual(charts[0]["series"], "销售额")

    def test_authored_chart_becomes_a_table_and_facts(self) -> None:
        book = _book(self.tmp)
        bundle = ingest.load(self.tmp)
        multimodal.augment(bundle, self.tmp)
        tables = [t for t in bundle.tables if t.sheet == "图表"]
        self.assertTrue(tables, f"no chart table in {[t.sheet for t in bundle.tables]}")
        frame = tables[0].df
        self.assertIn("销售额", list(frame.columns))
        self.assertEqual(float(frame["销售额"].sum()), 2140.0)
        facts = compute.extract(bundle)
        self.assertTrue(
            any("销售额" in (f.label or "") and len(f.points) == 3 for f in facts),
            [(f.label, f.points) for f in facts],
        )
        self.assertTrue(book.exists())

    def test_workbook_without_charts_is_quiet(self) -> None:
        self.assertEqual(multimodal.xlsx_charts(_book(self.tmp, with_chart=False)), [])


if __name__ == "__main__":
    unittest.main()

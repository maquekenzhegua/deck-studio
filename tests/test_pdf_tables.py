"""A PDF with a ruled table is data, not a picture of data.

The extractor exists, but nothing asserted it: the sample PDF is prose, so a
regression in `page.find_tables()` would have shipped silently and every
"upload a report and get charts" promise would quietly degrade to a text deck.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from deck import compute, ingest


def make_table_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    xs = [60, 200, 330, 470]
    ys = [80, 120, 160, 200, 240]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]), color=(0, 0, 0), width=0.9)
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y), color=(0, 0, 0), width=0.9)
    rows = [["Region", "Sales", "Growth"],
            ["East", "977", "34"],
            ["North", "509", "-4"],
            ["South", "654", "11"]]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            page.insert_text((xs[c] + 8, ys[r] + 26), value, fontsize=11)
    doc.save(str(path))
    doc.close()


class PdfTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dir = Path(tempfile.mkdtemp(prefix="deck-pdf-"))
        make_table_pdf(cls.dir / "report.pdf")
        cls.bundle = ingest.load(cls.dir)

    def test_the_table_is_found_with_its_page(self) -> None:
        tables = [t for t in self.bundle.tables if "/ 第1页表" in t.name.replace("report.pdf", "")]
        self.assertTrue(tables, [t.name for t in self.bundle.tables])
        self.assertGreaterEqual(len(tables[0].df), 3)
        self.assertGreaterEqual(len(tables[0].df.columns), 3)

    def test_values_survive_into_facts(self) -> None:
        facts = compute.extract(self.bundle)
        self.assertTrue(facts)
        numbers = [f.number for f in facts if getattr(f, "number", None) is not None]
        self.assertTrue(numbers, "表格里的数字没有变成事实")

    def test_the_columns_are_named_not_indexed(self) -> None:
        table = [t for t in self.bundle.tables if "第1页表" in t.name][0]
        joined = " ".join(str(c) for c in table.df.columns)
        self.assertIn("Region", joined)


if __name__ == "__main__":
    unittest.main()


class CoercionTest(unittest.TestCase):
    """Numbers become numbers; percentages stay text until a header says otherwise."""

    def test_thousands_separators_and_spaces_are_stripped(self) -> None:
        import pandas as pd
        df = pd.DataFrame({"Sales": ["1,278", " 977 ", "509"]})
        out = ingest._coerce_numbers(df)
        self.assertEqual(list(out["Sales"]), [1278, 977, 509])

    def test_percentages_are_left_alone(self) -> None:
        import pandas as pd
        df = pd.DataFrame({"Growth": ["34%", "-4%", "11%"]})
        out = ingest._coerce_numbers(df)
        self.assertEqual(out["Growth"].iloc[0], "34%")

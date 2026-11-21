"""The CLI is a second front door, so it gets the same editor as the window."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from deck import cli, compose, compute, ingest

DATA = Path(__file__).resolve().parent.parent / "samples" / "data"


class ReviewCommandTest(unittest.TestCase):
    def test_review_reads_a_plan_file(self):
        bundle = ingest.load(DATA)
        facts = compute.extract(bundle)
        plan = compose.draft(bundle, facts)
        path = Path(tempfile.mkdtemp()) / "plan.json"
        path.write_text(plan.to_json(), encoding="utf-8")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["review", str(DATA), "--plan", str(path)])

        self.assertEqual(code, 0)
        text = buf.getvalue()
        self.assertTrue(text.strip(), "review printed nothing")
        # Either a clean bill or a line per finding; both mention a page or a count.
        self.assertTrue("问题" in text or "页" in text, text[:120])

    def test_review_json_is_machine_readable(self):
        import json

        bundle = ingest.load(DATA)
        facts = compute.extract(bundle)
        plan = compose.draft(bundle, facts)
        path = Path(tempfile.mkdtemp()) / "plan.json"
        path.write_text(plan.to_json(), encoding="utf-8")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["review", str(DATA), "--plan", str(path), "--json"])

        rows = json.loads(buf.getvalue())
        self.assertIsInstance(rows, list)
        for row in rows:
            self.assertIn("page", row)
            self.assertIn(row["level"], ("block", "warn", "note"))


if __name__ == "__main__":
    unittest.main()


class TextOnlyBuildTest(unittest.TestCase):
    def test_build_from_a_memo_without_a_table(self):
        import tempfile, types
        from pathlib import Path
        from deck import cli
        with tempfile.TemporaryDirectory() as td:
            doc = Path(td) / "说明.md"
            doc.write_text("# 说明\n\n客户不是不愿意付钱，是我们没有把价值讲清楚。\n\n"
                           "下半年先把方案阶段缩短，再谈拉新。\n", encoding="utf-8")
            out = Path(td) / "deck.pptx"
            args = types.SimpleNamespace(input=str(doc.parent), out=str(out),
                                         offline=True, theme="editorial",
                                         slides=0, title="", classic=False,
                                         force=True, model="", base_url="")
            self.assertEqual(cli.cmd_build(args), 0)
            self.assertTrue(out.exists())

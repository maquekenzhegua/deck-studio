"""One-line edits are a product promise: if the sentence is understood, the
deck changes; if it is not, nothing changes and the message says why."""

from __future__ import annotations

import unittest

from deck import compose, compute, edit, ingest


class EditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        bundle = ingest.load("samples/data")
        cls.facts = compute.extract(bundle)
        cls.plan = compose.draft(bundle, cls.facts)

    def test_delete_a_page(self) -> None:
        n = len(self.plan.slides)
        plan, msg = edit.apply(self.plan, "第三页删掉")
        self.assertEqual(len(plan.slides), n - 1)
        self.assertEqual(len(self.plan.slides), n, "apply must not mutate its input")
        self.assertIn("删掉", msg)

    def test_move_a_page(self) -> None:
        first = self.plan.slides[2].title
        plan, _ = edit.apply(self.plan, "第3页上移")
        self.assertEqual(plan.slides[1].title, first)

    def test_retitle_one_page(self) -> None:
        plan, msg = edit.apply(self.plan, "第4页标题改成增长只在两个区")
        self.assertEqual(plan.slides[3].title, "增长只在两个区")
        self.assertIn("第 4 页", msg)

    def test_deck_title(self) -> None:
        plan, _ = edit.apply(self.plan, "总标题改成明年只做三件事")
        self.assertEqual(plan.deck_title, "明年只做三件事")

    def test_unknown_sentence_is_refused_not_guessed(self) -> None:
        plan, msg = edit.apply(self.plan, "让它更有冲击力")
        self.assertEqual(plan, self.plan)
        self.assertIn("没听懂", msg)


if __name__ == "__main__":
    unittest.main()

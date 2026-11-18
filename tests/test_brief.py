"""The brief decides what deck to build. If it is wrong the deck is wrong in a
way no amount of layout polish fixes, so its arithmetic is tested directly."""

from __future__ import annotations

import json
import unittest

import pandas as pd

from deck import brief as B
from deck.ingest import Bundle, Doc, Picture, Table


def bundle(tables=1, docs=1, pics=0, skipped=0) -> Bundle:
    df = pd.DataFrame({"区域": ["华东", "华北"], "销售额": [977.2, 509.3]})
    return Bundle(
        tables=[Table(f"表{i}", df, f"/x/t{i}.xlsx") for i in range(tables)],
        docs=[Doc(f"文档{i}", "2024 年度经营小结", f"/x/d{i}.docx") for i in range(docs)],
        pictures=[Picture(f"p{i}", f"图{i}.png", f"/x/p{i}.png", 800, 600) for i in range(pics)],
        skipped=[f"/x/bad{i}.xls" for i in range(skipped)],
    )


def filled(**kw) -> B.Brief:
    b = B.Brief(coverage=1.0)
    for k in B.SLOTS:
        b.slots[k] = B.Slot(k)
    for k, v in kw.items():
        b.slots[k] = B.Slot(k, v, "原话", "quoted")
    return b


class ScanTest(unittest.TestCase):
    def test_coverage_counts_unreadable_files(self):
        cov, notes = B.scan(bundle(tables=2, docs=1, skipped=1))
        self.assertAlmostEqual(cov, 0.75)
        self.assertTrue(any("读不了" in n for n in notes))

    def test_empty_input_has_zero_coverage(self):
        cov, _ = B.scan(Bundle())
        self.assertEqual(cov, 0.0)

    def test_multiple_tables_is_called_out(self):
        _, notes = B.scan(bundle(tables=3))
        self.assertTrue(any("主表" in n for n in notes))


class ConfidenceTest(unittest.TestCase):
    def test_nothing_known_scores_zero(self):
        self.assertEqual(filled().confidence, 0.0)

    def test_all_quoted_scores_one(self):
        b = filled(**{k: "x" for k in B.SLOTS})
        self.assertEqual(b.confidence, 1.0)

    def test_coverage_multiplies(self):
        b = filled(**{k: "x" for k in B.SLOTS})
        b.coverage = 0.5
        self.assertEqual(b.confidence, 0.5)

    def test_inferred_evidence_scores_lower_than_a_quote(self):
        b = filled(decision="批预算")
        quoted = b.confidence
        b.slots["decision"] = B.Slot("decision", "批预算", "", "inferred")
        self.assertLess(b.confidence, quoted)

    def test_quoted_without_a_quote_is_downgraded(self):
        # The model can claim "quoted" and supply nothing. Evidence is the
        # thing we can check, so it wins.
        s = B.Slot("decision", "批预算", "", "quoted")
        self.assertEqual(s.score, B.S_QUOTED)
        b = B.Brief()
        b.slots["decision"] = B.Slot("decision", "批预算", "", "inferred")
        self.assertEqual(b.get("decision").score, B.S_STRONG)


class ModeTest(unittest.TestCase):
    def _with(self, value: float) -> B.Brief:
        b = B.Brief(coverage=1.0)
        b.slots = {k: B.Slot(k) for k in B.SLOTS}
        # spread the score across slots so the weighted sum lands on `value`
        per = value * 100 / len(B.SLOTS)
        for k in B.SLOTS:
            b.slots[k] = B.Slot(k, "x", "", "quoted")
            b.slots[k].source = "none"
        b.slots["decision"] = B.Slot("decision", "x", "", "quoted")
        b.slots["audience"] = B.Slot("audience", "x", "", "quoted")
        b.slots["occasion"] = B.Slot("occasion", "x", "", "quoted")
        b.slots["avoid"] = B.Slot("avoid", "x", "", "quoted")
        return b

    def test_thresholds(self):
        # decision 25 + audience 20 + occasion 15 + avoid 15 + length 15 = 0.90
        b = self._with(0.9)
        self.assertEqual(b.mode, "auto")
        self.assertEqual(b.ask_count(), 0)
        # dropping length (15) lands on 0.75, which is still auto: the
        # boundary belongs to the calmer mode.
        b.slots["length"] = B.Slot("length")
        self.assertEqual(b.mode, "auto")
        # dropping avoid as well gives 0.60 -> ask two questions
        b.slots["avoid"] = B.Slot("avoid")
        self.assertEqual(b.confidence, 0.6)
        self.assertEqual(b.mode, "ask_min")
        self.assertEqual(b.ask_count(), 2)

    def test_zero_confidence_blocks(self):
        b = filled()
        self.assertEqual(b.mode, "blocked")
        self.assertEqual(b.ask_count(), 1)


class ChooseTest(unittest.TestCase):
    def test_questions_the_files_answer_are_dropped(self):
        b = filled(decision="批预算")          # quoted
        ids = [q.id for q in B.choose(bundle(), b)]
        self.assertNotIn("Q_decision", ids)

    def test_conflict_question_needs_both_tables_and_docs(self):
        # Eligibility, not ranking: a doc/table conflict is unanswerable when
        # there is no document to conflict with.
        b = filled()
        ids = [q.id for q in B._candidates(bundle(tables=1, docs=0), b)]
        self.assertNotIn("Q_conflict", ids)
        ids = [q.id for q in B._candidates(bundle(tables=1, docs=1), b)]
        self.assertIn("Q_conflict", ids)

    def test_question_count_follows_the_mode(self):
        b = filled()                            # 0.00 -> blocked -> 1
        self.assertEqual(b.mode, "blocked")
        self.assertEqual(len(B.choose(bundle(), b)), 1)
        b.slots["decision"] = B.Slot("decision", "批预算", "", "quoted")
        b.slots["audience"] = B.Slot("audience", "管理层", "", "quoted")
        self.assertEqual(b.confidence, 0.45)     # -> ask_full -> 4
        self.assertEqual(len(B.choose(bundle(), b)), 4)

    def test_ranked_by_impact_per_cost(self):
        b = filled()
        qs = B.choose(bundle(), b)
        pri = [q.priority(b) for q in qs]
        self.assertEqual(pri, sorted(pri, reverse=True))


class FillTest(unittest.TestCase):
    def test_defaults_are_marked_assumed(self):
        b = filled()
        b.questions = B.choose(bundle(), b)
        B.fill_defaults(b)
        assumed = [s for s in b.slots.values() if s.assumed]
        self.assertTrue(assumed)
        self.assertTrue(all(s.value for s in assumed))

    def test_block_states_the_assumption(self):
        b = filled()
        b.questions = B.choose(bundle(), b)
        B.fill_defaults(b)
        text = b.block()
        self.assertIn("假设", text)

    def test_answers_override_and_are_not_assumed(self):
        b = filled()
        b.questions = B.choose(bundle(), b)
        q = b.questions[0]
        B.apply_answers(b, {q.id: "批预算"})
        self.assertFalse(b.get(q.slot).assumed)
        self.assertEqual(b.get(q.slot).evidence, "用户回答")


class RoundTripTest(unittest.TestCase):
    def test_json_survives_a_round_trip(self):
        b = filled(decision="批预算", audience="管理层")
        b.questions = B.choose(bundle(), b)
        back = B.Brief.from_json(b.to_json())
        self.assertEqual(back.confidence, b.confidence)
        self.assertEqual(back.get("decision").value, "批预算")
        self.assertEqual([q.id for q in back.questions], [q.id for q in b.questions])

    def test_make_without_a_client_asks_rather_than_guesses(self):
        b = B.make(bundle(), [], "", client=None)
        self.assertEqual(b.mode, "blocked")
        self.assertTrue(b.questions)


if __name__ == "__main__":
    unittest.main()

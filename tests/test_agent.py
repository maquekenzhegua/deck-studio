"""The model leads the argument; the program holds the numbers.

These tests drive the whole loop with a fake client, because the behaviour
that matters is not "does the API answer" but "what happens when it answers
badly": invented figures, a broken outline, a timeout, a repair that does not
improve anything.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from deck import agent, compute, ingest, verify
from deck.llm import LLMError

DATA = Path(__file__).resolve().parent.parent / "samples" / "data"


class Fake:
    """Stands in for `deck.llm.Client`; records what it was asked."""

    def __init__(self, replies, model="fake-1"):
        self.replies = list(replies)
        self.model = model
        self.calls = []

    def json(self, system, user, **kwargs):
        self.calls.append(user[:120])
        if not self.replies:
            raise LLMError("no more replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def outline(titles=("华东在涨，北方在退", "订单比客户多", "明年只做三件事")):
    slides = [{"layout": "title", "title": "增长靠华东，失血在北方"}]
    for i, title in enumerate(titles[:-1]):
        slides.append({"layout": "bullets", "title": title, "bullets": ["一句", "两句"]})
    slides.append({"layout": "closing", "title": titles[-1], "bullets": ["压华东", "止北方"]})
    return {"deck_title": "增长靠华东", "subtitle": "", "slides": slides}


class AgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = ingest.load(DATA)
        cls.facts = compute.extract(cls.bundle)

    def test_without_a_client_it_is_offline(self):
        result = agent.plan(self.bundle, self.facts)
        self.assertEqual(result.planner, "offline")
        self.assertEqual(result.revisions, 0)
        self.assertTrue(result.plan.slides)

    def test_a_clean_model_plan_is_used_as_is(self):
        fake = Fake([outline()])
        result = agent.plan(self.bundle, self.facts, client=fake)
        self.assertEqual(result.planner, "model")
        self.assertEqual(result.revisions, 0)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result.plan.slides[0].layout, "title")
        self.assertEqual(verify.check(result.plan, self.facts), [])

    def test_a_fabricated_number_triggers_one_repair(self):
        broken = outline(titles=("华东增长 999%", "订单比客户多", "明年只做三件事"))
        fixed = outline()
        fake = Fake([broken, fixed])
        result = agent.plan(self.bundle, self.facts, client=fake)
        self.assertEqual(result.planner, "model")
        self.assertEqual(result.revisions, 1)
        self.assertEqual(len(fake.calls), 2)
        self.assertNotIn("999", result.plan.slides[1].title)

    def test_a_repair_that_changes_nothing_keeps_the_offline_deck(self):
        broken = outline(titles=("华东增长 999%", "订单比客户多", "明年只做三件事"))
        fake = Fake([broken, broken])
        result = agent.plan(self.bundle, self.facts, client=fake)
        self.assertEqual(result.planner, "offline")
        self.assertTrue(result.problems)

    def test_a_dead_endpoint_falls_back_instead_of_raising(self):
        fake = Fake([LLMError("HTTP 401: invalid key")])
        result = agent.plan(self.bundle, self.facts, client=fake)
        self.assertEqual(result.planner, "offline")
        self.assertTrue(any("401" in line for line in result.log))

    def test_an_empty_outline_falls_back(self):
        fake = Fake([{"slides": []}])
        result = agent.plan(self.bundle, self.facts, client=fake)
        self.assertEqual(result.planner, "offline")

    def test_summary_counts_the_revisions_it_claims(self):
        result = agent.plan(self.bundle, self.facts, client=Fake([outline()]))
        self.assertEqual(result.summary(), "fake-1 主导")
        result.revisions = 2
        self.assertIn("修订 2 次", result.summary())


if __name__ == "__main__":
    unittest.main()


class VariantTest(unittest.TestCase):
    """The model may choose how a chart is drawn — within reason."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = ingest.load(DATA)
        cls.facts = compute.extract(cls.bundle)

    def _deck(self, variant, fact):
        return {
            "deck_title": "产品线分化",
            "slides": [
                {"layout": "title", "title": "一条线在加速，另一条没有跟上"},
                {"layout": "chart", "title": "三条线并不同步", "fact": fact,
                 "variant": variant, "bullets": ["硬件最陡", "服务走平", "差距拉大"]},
                {"layout": "closing", "title": "先把资源压到强的一条线"},
            ],
        }

    def test_variant_reaches_the_plan(self) -> None:
        multi = next(f for f in self.facts if f.series)
        res = agent.plan(self.bundle, self.facts,
                         client=Fake([self._deck("multi", multi.id)]))
        self.assertEqual(res.planner, "model", res.log)
        self.assertEqual(res.plan.slides[1].variant, "multi")

    def test_unknown_variant_is_dropped(self) -> None:
        res = agent.plan(self.bundle, self.facts,
                         client=Fake([self._deck("sunburst", "f1")]))
        self.assertEqual([s.layout for s in res.plan.slides][1], "chart")
        self.assertEqual(res.plan.slides[1].variant, "")

    def test_heatmap_needs_two_facts(self) -> None:
        res = agent.plan(self.bundle, self.facts,
                         client=Fake([self._deck("heatmap", "f1")]))
        self.assertEqual(res.plan.slides[1].variant, "")

    def test_style_notes_do_not_discard_a_model_deck(self) -> None:
        """Three chart pages in a row is a note, not a reason to fall back."""
        deck = {
            "deck_title": "三张图",
            "slides": [
                {"layout": "title", "title": "先看三个数字"},
                {"layout": "chart", "title": "销售额在涨", "fact": "f1"},
                {"layout": "chart", "title": "订单数在涨", "fact": "f5"},
                {"layout": "chart", "title": "客单价在涨", "fact": "f9"},
                {"layout": "closing", "title": "把结论带走"},
            ],
        }
        res = agent.plan(self.bundle, self.facts, client=Fake([deck, deck]))
        self.assertEqual(res.planner, "model", res.log)
        self.assertTrue(any("版式建议" in line for line in res.log), res.log)


class ReviseTest(unittest.TestCase):
    """A sentence in, a changed outline out — by the model when there is one."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = ingest.load(DATA)
        cls.facts = compute.extract(cls.bundle)
        cls.base = agent.plan(cls.bundle, cls.facts).plan

    def test_revision_replaces_the_outline(self):
        shorter = outline()
        shorter["slides"] = [s for s in shorter["slides"] if s["layout"] != "bullets"][:2]
        fake = Fake([shorter])
        got = agent.revise(self.base, self.facts, "删掉中间那两页要点", client=fake)
        self.assertIsNotNone(got)
        revised, message = got
        self.assertLess(len(revised.slides), len(self.base.slides))
        self.assertIn("模型改稿", message)

    def test_an_invented_figure_rejects_the_whole_revision(self):
        bad = outline()
        bad["slides"].append({"layout": "bullets", "title": "增长 999%",
                              "bullets": ["一句"]})
        fake = Fake([bad])
        got = agent.revise(self.base, self.facts, "加一页", client=fake)
        # The rules keep the numbers: an invented figure is not a partial win.
        self.assertIsNone(got)

    def test_a_broken_answer_is_not_a_revision(self):
        fake = Fake([{"slides": []}])
        self.assertIsNone(agent.revise(self.base, self.facts, "改一下", client=fake))

    def test_a_timeout_is_not_a_revision(self):
        fake = Fake([LLMError("HTTP 401")])
        self.assertIsNone(agent.revise(self.base, self.facts, "改一下", client=fake))

    def test_an_empty_instruction_is_not_a_call(self):
        fake = Fake([])
        self.assertIsNone(agent.revise(self.base, self.facts, "   ", client=fake))
        self.assertEqual(fake.calls, [])

class SlowModelTest(unittest.TestCase):
    """A timeout should shrink the ask once, not give up on the model."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = ingest.load(DATA)
        cls.facts = compute.extract(cls.bundle)

    def test_a_timeout_retries_with_fewer_pages(self):
        shorter = outline()
        fake = Fake([LLMError("timed out"), shorter])
        result = agent.plan(self.bundle, self.facts, client=fake)
        self.assertEqual(len(fake.calls), 2)
        # The retry must actually ask for less, or it is the same timeout again.
        self.assertTrue(any("改为 5 页" in line for line in result.log))
        self.assertTrue(result.plan.slides)
        self.assertTrue(any("缩短篇幅" in line for line in result.log))

    def test_two_timeouts_fall_back_and_say_so(self):
        fake = Fake([LLMError("timed out"), LLMError("timed out again")])
        result = agent.plan(self.bundle, self.facts, client=fake)
        self.assertEqual(result.planner, "offline")
        self.assertTrue(any("模型不可用" in line for line in result.log))


class PitchTest(unittest.TestCase):
    """The model proposes angles before pages; junk must not reach the UI."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = ingest.load(DATA)
        cls.facts = compute.extract(cls.bundle)

    def test_three_readable_angles_come_back(self):
        fake = Fake([{"ideas": [
            {"title": "华东撑住了增长", "angle": "把增量归到一个区", "why": "f10 f12"},
            {"title": "产品线在换挡", "angle": "老产品退、新硬件上", "why": "f2"},
            {"title": "漏斗漏在方案", "angle": "先修中段", "why": "f16"},
            {"title": "多出来的一条", "angle": "不该出现", "why": ""},
        ]}])
        ideas = agent.pitch(self.bundle, self.facts, client=fake, n=3)
        self.assertEqual(len(ideas), 3)
        self.assertEqual(ideas[0]["title"], "华东撑住了增长")
        self.assertTrue(all(i["title"] and i["angle"] for i in ideas))

    def test_angles_without_a_title_are_dropped(self):
        fake = Fake([{"ideas": [{"angle": "只有角度", "why": ""},
                                {"title": "有标题", "angle": "有角度", "why": ""}]}])
        ideas = agent.pitch(self.bundle, self.facts, client=fake)
        self.assertEqual([i["title"] for i in ideas], ["有标题"])

    def test_an_empty_answer_is_an_error_not_an_empty_list(self):
        fake = Fake([{"ideas": []}])
        with self.assertRaises(ValueError):
            agent.pitch(self.bundle, self.facts, client=fake)


class SelfReviewTest(unittest.TestCase):
    """The model should fix its own content-level mistakes, unasked."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = ingest.load(DATA)
        cls.facts = compute.extract(cls.bundle)

    def test_a_numeric_title_is_sent_back_and_fixed(self):
        bad = outline(titles=("2024", "订单比客户多", "明年只做三件事"))
        good = outline()
        fake = Fake([bad, good])
        result = agent.plan(self.bundle, self.facts, client=fake)
        self.assertEqual(result.planner, "model")
        self.assertEqual(len(fake.calls), 2)
        self.assertTrue(any("自查发现" in line for line in result.log))
        self.assertNotEqual(result.plan.slides[1].title, "2024")

    def test_a_broken_correction_does_not_replace_the_draft(self):
        bad = outline(titles=("2024", "订单比客户多", "明年只做三件事"))
        broken = outline(titles=("增长 999%", "订单比客户多", "明年只做三件事"))
        fake = Fake([bad, broken])
        result = agent.plan(self.bundle, self.facts, client=fake)
        self.assertTrue(any("数字对不上" in line for line in result.log))
        self.assertEqual(result.plan.slides[1].title, "2024")


class SubtitleTest(unittest.TestCase):
    """Planning scaffolding must never reach a slide."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = ingest.load(DATA)
        cls.facts = compute.extract(cls.bundle)

    def test_a_subtitle_that_names_facts_is_dropped(self):
        fake = Fake([outline()])
        result = agent.plan(self.bundle, self.facts, client=fake,
                            subtitle="靠 f13、f15、f17 三条事实咬合站得住")
        self.assertNotIn("f13", result.plan.subtitle or "")

    def test_a_real_subtitle_survives(self):
        fake = Fake([outline()])
        result = agent.plan(self.bundle, self.facts, client=fake,
                            subtitle="2026 年经营复盘")
        self.assertEqual(result.plan.subtitle, "2026 年经营复盘")


class ChartShapeTest(unittest.TestCase):
    """The model names a chart; the referee may overrule it once."""

    def test_a_share_title_gets_a_ring_even_if_the_model_said_chart(self) -> None:
        bundle = ingest.load(DATA)
        facts = compute.extract(bundle)
        pick = next(f for f in facts
                    if "产品线" in f.label and f.unit == "money" and 3 <= len(f.points) <= 5)
        reply = {
            "deck_title": "增长靠产品结构", "subtitle": "",
            "slides": [
                {"layout": "title", "title": "增长靠产品结构"},
                {"layout": "chart", "title": "智能硬件吃掉一半收入", "fact": pick.id},
                {"layout": "closing", "title": "接下来", "bullets": ["压华东", "止北方"]},
            ],
        }
        result = agent.plan(bundle, facts, client=Fake([reply]))
        self.assertEqual(result.planner, "model")
        self.assertEqual(result.plan.slides[1].variant, "donut")
        self.assertTrue(any("占比环" in line for line in result.log))

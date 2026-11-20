"""The model writes what to say, not what to show.

A deck that only exists on screen leaves the presenter to invent the words in
the room. The script is prose, so it is allowed to be opinionated — but it is
not allowed to carry a figure, because nothing downstream verifies prose.
"""
import unittest

from deck import agent
from deck.compute import Fact
from deck.plan import Plan, Slide


def fact(fid="f1", number="34.0%", unit="ratio"):
    return Fact(id=fid, kind="breakdown", label="同比", value=number, raw=0.34,
                source="x.xlsx / 区域", column="同比", unit=unit, agg="mean",
                chart="bar", points=[("华东", 0.34)], number=number)


def plan() -> Plan:
    """A fresh plan per test: `notes()` writes scripts onto the plan it gets."""
    return Plan("d", "", [
        Slide(layout="title", title="增长靠华东"),
        Slide(layout="chart", title="华东领涨", fact="f1", bullets=["西南紧随"]),
        Slide(layout="closing", title="接下来做什么"),
    ])


class Fake:
    def __init__(self, reply):
        self.reply = reply
        self.model = "fake-notes"
        self.calls = []

    def json(self, system, user, **kwargs):
        self.calls.append(user[:80])
        return self.reply


class NotesTest(unittest.TestCase):
    def test_a_clean_script_is_attached_to_its_page(self):
        got = agent.notes(plan(), [fact()], client=Fake({"notes": [
            {"page": 1, "say": "先把结论放在最前面，后面每一页都在为它找证据。"},
            {"page": 2, "say": "关键不是平均值，而是最好和最差之间差了多少。"},
        ]}))
        self.assertIsNotNone(got)
        written, msg = got
        self.assertIn("2", msg)
        self.assertTrue(written.slides[0].script.startswith("先把结论"))
        self.assertTrue(written.slides[1].script)

    def test_a_script_with_a_digit_is_dropped(self):
        got = agent.notes(plan(), [fact()], client=Fake({"notes": [
            {"page": 1, "say": "华东贡献了 34% 的增长，这一点最重要。"},
            {"page": 2, "say": "北部两个区同时在退，这是明年的首要问题。"},
        ]}))
        written, _ = got
        self.assertEqual(written.slides[0].script, "")
        self.assertTrue(written.slides[1].script)

    def test_out_of_range_pages_are_ignored(self):
        got = agent.notes(plan(), [fact()], client=Fake({"notes": [
            {"page": 9, "say": "页码超出范围的句子不应该落到任何一页上。"},
        ]}))
        self.assertIsNone(got)

    def test_a_fact_placeholder_becomes_a_readable_number(self):
        spoken = agent._spoken("华东 {{f1}} 领涨", {"f1": fact()})
        self.assertEqual(spoken, "华东 34.0% 领涨")

    def test_no_client_means_no_request(self):
        self.assertIsNone(agent.notes(plan(), [fact()], client=None))

    def test_a_short_sentence_is_not_a_script(self):
        got = agent.notes(plan(), [fact()], client=Fake({"notes": [
            {"page": 1, "say": "好"},
        ]}))
        self.assertIsNone(got)


if __name__ == "__main__":
    unittest.main()

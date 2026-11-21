import unittest

from deck import tighten
from deck.plan import Plan, Slide


def plan(*titles, bullets=None):
    slides = [Slide(layout="bullets", title=t, bullets=(bullets or [])) for t in titles]
    return Plan(deck_title="t", subtitle="s", slides=slides)


class TrimTest(unittest.TestCase):
    def test_short_title_is_left_alone(self):
        self.assertEqual(tighten.trim("华东领涨，华北失血"), "华东领涨，华北失血")

    def test_long_title_cuts_at_the_last_punctuation(self):
        title = "华东一个区顶住全盘增长，华北和东北同时失血，问题卡在方案转化这一环"
        out = tighten.trim(title)
        self.assertLessEqual(len(out), tighten.LIMIT)
        self.assertTrue(title.startswith(out))
        self.assertFalse(out.endswith(("，", "、")))
        self.assertGreaterEqual(len(out), 8)

    def test_long_title_without_punctuation_is_kept(self):
        title = "华东领涨华北东北失血效率卡在方案转化这一环上并且明年还要继续投入更多资源"
        self.assertEqual(tighten.trim(title), title)

    def test_bullets_are_capped(self):
        out, log = tighten.apply(plan("短标题", bullets=["a", "b", "c", "d", "e", "f"]))
        self.assertEqual(len(out.slides[0].bullets), tighten.BULLETS)
        self.assertTrue(any("6 条收到 4 条" in line for line in log))

    def test_plan_titles_are_trimmed_and_logged(self):
        long = "华东一个区顶住全盘增长，华北和东北同时失血，问题卡在方案转化这一环"
        out, log = tighten.apply(plan(long))
        self.assertLessEqual(len(out.slides[0].title), tighten.LIMIT)
        self.assertTrue(any("标题从" in line for line in log))


if __name__ == "__main__":
    unittest.main()

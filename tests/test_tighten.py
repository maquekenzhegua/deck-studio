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


class FitPagesTest(unittest.TestCase):
    def _plan(self, n=12):
        slides = [Slide(layout="title", title="封面")]
        slides += [Slide(layout="section", title="第一章")] if n > 3 else []
        slides += [Slide(layout="chart", title=f"页{i}", fact="f1")
                   for i in range(n - 2)]
        slides += [Slide(layout="closing", title="收尾")]
        return Plan(deck_title="t", subtitle="s", slides=slides)

    def test_keeps_the_skeleton_and_hits_the_number(self):
        plan, log = tighten.fit_pages(self._plan(12), 6)
        self.assertEqual(len(plan.slides), 6)
        self.assertEqual(plan.slides[0].layout, "title")
        self.assertEqual(plan.slides[-1].layout, "closing")
        self.assertIn("section", [s.layout for s in plan.slides])
        self.assertTrue(log and "6" in log[0])

    def test_a_budget_above_the_length_changes_nothing(self):
        plan, log = tighten.fit_pages(self._plan(6), 12)
        self.assertEqual(len(plan.slides), 7)
        self.assertEqual(log, [])

    def test_order_is_preserved(self):
        plan, _ = tighten.fit_pages(self._plan(12), 5)
        titles = [s.title for s in plan.slides]
        self.assertEqual(titles[0], "封面")
        self.assertEqual(titles[-1], "收尾")
        self.assertEqual(len(titles), len(set(titles)))

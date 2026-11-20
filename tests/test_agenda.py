"""The section page is an agenda, so it has to admit when it stops early.

A chapter that lists five pages and then runs for thirteen reads as a promise
the deck breaks by page six. One muted line fixes it.
"""
import tempfile
import unittest
from pathlib import Path

from pptx import Presentation

from deck.plan import Plan, Slide
from deck.render2 import build


class AgendaTest(unittest.TestCase):
    def _render(self, n_after: int) -> str:
        slides = [Slide(layout="title", title="封面")]
        slides.append(Slide(layout="section", title="先看趋势", note="结论在前，证据在后。"))
        for i in range(n_after):
            slides.append(Slide(layout="bullets", title=f"第 {i + 1} 个判断"))
        slides.append(Slide(layout="closing", title="接下来做什么"))
        plan = Plan("测试", "", slides)
        out = Path(tempfile.mkdtemp()) / "agenda.pptx"
        build(plan, [], [], str(out), theme="editorial")
        prs = Presentation(str(out))
        return "\n".join(
            sh.text_frame.text
            for s in prs.slides
            for sh in s.shapes
            if sh.has_text_frame
        )

    def test_a_long_chapter_says_how_long_it_is(self):
        text = self._render(6)
        self.assertIn("共 9 页", text)
        self.assertIn("…", text)

    def test_a_short_chapter_just_states_the_total(self):
        text = self._render(2)
        self.assertIn("共 5 页", text)


if __name__ == "__main__":
    unittest.main()

"""Wording rules that keep a page honest.

Two failures shipped once and are cheap to test:

1. A growth-rate breakdown was described as "华东占 41%". A rate does not add
   up, so that share has no unit and reads as a share of revenue.
2. A ranked list (regions by sales) used the funnel sentence "从华东到华南
   收窄最多". Nothing narrows in a ranking.

Both are asserted on the rendered deck, not on a helper, because the sentence
is assembled from several branches.
"""

from pathlib import Path
import tempfile
import unittest

from pptx import Presentation

from deck import compute, ingest, render2
from deck.plan import Plan, Slide


DATA = Path(__file__).resolve().parent.parent / "samples" / "data"


def text_of(pptx: Path) -> str:
    prs = Presentation(str(pptx))
    out = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                out.append(shape.text_frame.text)
    return "\n".join(out)


class WordingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bundle = ingest.load(DATA)
        cls.facts = compute.extract(bundle)
        cls.ratio = next(
            f for f in cls.facts if f.unit == "ratio" and "区域" in f.label and len(f.points) > 2
        )
        cls.money = next(
            f for f in cls.facts if f.unit == "money" and "区域" in f.label and len(f.points) > 2
        )

    def render(self, slide: Slide) -> str:
        plan = Plan("措辞检查", "", [slide])
        tmp = Path(tempfile.mkdtemp(prefix="deck-wording-")) / "deck.pptx"
        render2.build(plan, self.facts, [], tmp, theme="editorial")
        return text_of(tmp)

    def test_growth_rate_names_its_ends_instead_of_a_share(self):
        text = self.render(Slide("key_figure", "华东增速最高", fact=self.ratio.id))
        self.assertIn("最高", text)
        self.assertIn("最低", text)
        self.assertNotIn("占 ", text)

    def test_a_ranking_does_not_borrow_the_funnel_sentence(self):
        text = self.render(Slide("table", "华东规模第一", fact=self.money.id))
        self.assertNotIn("收窄最多", text)
        self.assertIn("最高", text)
        self.assertIn("最低", text)


if __name__ == "__main__":
    unittest.main()

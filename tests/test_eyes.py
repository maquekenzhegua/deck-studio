"""The vision pass: it may only talk about layout, and every note needs a fix."""

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from deck import eyes


def pages(n=3, tmp=None):
    out = []
    for i in range(n):
        p = Path(tmp) / f"{i+1:02d}.png"
        Image.new("RGB", (1600, 900), (250, 248, 244)).save(p)
        out.append(p)
    return out


class Fake:
    def __init__(self, payload):
        self.payload = payload
        self.seen = None
        self.model = "fake-vl"

    def vision(self, system, text, images, **kw):
        # The temporary shrunk copies die with look(), so measure them now.
        with Image.open(images[0]) as first:
            self.first = first.size
        self.seen = list(images)
        self.sizes = []
        for p in images:
            with Image.open(p) as im:
                self.sizes.append(im.size)
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)


class LookTest(unittest.TestCase):
    def test_notes_come_back_in_page_order(self):
        payload = {"notes": [
            {"page": 2, "problem": "右栏压到页脚", "fix": "整栏上移 0.3 英寸"},
            {"page": 1, "problem": "标题与图距离太近", "fix": "标题上移 0.2 英寸"},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            got = eyes.look(pages(3, tmp), Fake(payload))
        self.assertEqual([n.page for n in got], [2, 1])
        self.assertEqual(got[0].fix, "整栏上移 0.3 英寸")

    def test_a_note_without_a_fix_is_dropped(self):
        payload = {"notes": [{"page": 1, "problem": "看着挤", "fix": ""}]}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(eyes.look(pages(2, tmp), Fake(payload)), [])

    def test_a_page_outside_the_deck_is_ignored(self):
        payload = {"notes": [{"page": 99, "problem": "越界", "fix": "删掉"}]}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(eyes.look(pages(2, tmp), Fake(payload)), [])

    def test_prose_around_the_json_is_tolerated(self):
        raw = '这是结果：\n```json\n{"notes":[{"page":1,"problem":"图太小","fix":"放大到 8 英寸宽"}]}\n```'
        with tempfile.TemporaryDirectory() as tmp:
            got = eyes.look(pages(1, tmp), Fake(raw))
        self.assertEqual(len(got), 1)
        self.assertIn("放大", got[0].fix)

    def test_images_are_downscaled_before_they_are_sent(self):
        fake = Fake({"notes": []})
        with tempfile.TemporaryDirectory() as tmp:
            eyes.look(pages(2, tmp), fake)
        for w, h in fake.sizes:
            self.assertLessEqual(w, 960)
            self.assertGreater(h, 0)

    def test_at_most_eight_pages_go_to_the_model(self):
        fake = Fake({"notes": []})
        with tempfile.TemporaryDirectory() as tmp:
            eyes.look(pages(10, tmp), fake)
        self.assertEqual(len(fake.seen), eyes.MAX_PAGES)

    def test_prompt_names_pages_and_forbids_new_pages(self):
        text = eyes.as_prompt([eyes.Note(2, "右栏空", "把读法上移")])
        self.assertIn("第 2 页", text)
        self.assertNotIn("增加一页", text)

    def test_summary_counts_and_lists_pages(self):
        got = eyes.summary([eyes.Note(1, "a", "b"), eyes.Note(4, "c", "d")])
        self.assertIn("2 处", got)
        self.assertIn("1、4", got)

    def test_no_pages_is_an_error_not_an_empty_success(self):
        with self.assertRaises(ValueError):
            eyes.look([], Fake({"notes": []}))


if __name__ == "__main__":
    unittest.main()

"""The desktop app is a product surface, not a demo script.

These tests drive the same HTTP API the window uses: upload a set of files,
get a plan back, change the title, read the metadata the UI renders. They do
not open PowerPoint — the preview export is exercised by hand, because a test
suite that launches Office is a test suite nobody runs.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="deck-app-"))
os.environ["DECK_HOME"] = str(HOME)

from fastapi.testclient import TestClient  # noqa: E402

from app.server import app  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "samples" / "data"


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)


    def test_settings_round_trip_through_the_api(self) -> None:
        default = self.client.get("/api/settings").json()
        self.assertIn(default["mode"], {"offline", "model"})
        self.assertGreaterEqual(len(default["presets"]), 4)

        posted = self.client.post("/api/settings", json={
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "sk-1234567890abcdef",
        }).json()
        self.assertEqual(posted["settings"]["mode"], "model")
        self.assertNotIn("1234567890", posted["settings"]["text"]["key"])
        self.assertTrue(posted["settings"]["text"]["has_key"])

        state = self.client.get("/api/state").json()
        self.assertEqual(state["settings"]["mode"], "model")

        bad = self.client.post("/api/settings/test", json={
            "base_url": "http://127.0.0.1:9/v1", "model": "x", "api_key": "k"}).json()
        self.assertFalse(bad["ok"])
        self.assertTrue(bad["message"])

        # leave the shared app offline so later tests do not try to reach a model
        self.client.post("/api/settings", json={})
        for name in ("DECK_BASE_URL", "DECK_API_KEY", "DECK_MODEL",
                     "DECK_VISION_URL", "DECK_VISION_KEY", "DECK_VISION_MODEL"):
            os.environ.pop(name, None)

    def test_health(self) -> None:
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("ok"), True)

    def test_index_is_the_window(self) -> None:
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Deck Studio", r.text)

    def test_meta_lists_themes(self) -> None:
        meta = self.client.get("/api/meta").json()
        ids = [t["id"] for t in meta["themes"]]
        self.assertIn("editorial", ids)
        self.assertEqual(len(ids), 5)
        self.assertIn("title", meta["layouts"])

    def test_sample_loads(self) -> None:
        r = self.client.post("/api/sample")
        self.assertEqual(r.status_code, 200)
        state = self.client.get("/api/state").json()
        self.assertGreaterEqual(len(state["facts"]), 15)
        self.assertGreaterEqual(len(state["plan"]["slides"]), 8)
        self.assertTrue(state["plan"]["deck_title"])

    def test_uploaded_pptx_becomes_a_selectable_theme(self) -> None:
        self.client.post("/api/sample")
        themes = {t["id"]: t for t in self.client.get("/api/meta").json()["themes"]}
        self.assertIn("custom", themes)
        self.assertTrue(themes["custom"]["note"].startswith("取自"))
        r = self.client.post("/api/edit", json={"text": "换成模板"}).json()
        self.assertEqual(r["theme"], "custom")

    def test_upload_skips_junk_and_keeps_good_files(self) -> None:
        files = [
            ("files", ("坏文件.bin", b"\x00\x01\x02junk", "application/octet-stream")),
            ("files", ("经营小结.docx", (DATA / "经营小结.docx").read_bytes(),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
        ]
        r = self.client.post("/api/upload", files=files)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["skipped"], ["坏文件.bin"])
        self.assertEqual(len(body["saved"]), 1)
        self.assertTrue(body["saved"][0].endswith(".docx"), body["saved"])

    def test_plan_follows_the_brief(self) -> None:
        self.client.post("/api/sample")
        r = self.client.post("/api/plan", json={
            "brief": "给董事会看的季度复盘，重点讲增长来自哪里",
            "deck_title": "增长只在两个区，其余在失血",
            "subtitle": "Q3 经营复盘",
        })
        self.assertEqual(r.status_code, 200)
        plan = r.json()["plan"]
        self.assertEqual(plan["deck_title"], "增长只在两个区，其余在失血")
        self.assertEqual(plan["subtitle"], "Q3 经营复盘")
        self.assertEqual(plan["slides"][0]["layout"], "title")

    def test_every_slide_offers_a_layout_switch(self) -> None:
        html = self.client.get("/").text
        self.assertIn("LAYOUT_OPTS", html)
        for name in ("封面", "章节", "引语", "原文摘录", "全幅图"):
            self.assertIn(name, html)

    def test_state_reports_where_files_live(self) -> None:
        state = self.client.get("/api/state").json()
        self.assertTrue(state["home"].startswith(str(HOME.parent)))


if __name__ == "__main__":
    unittest.main()

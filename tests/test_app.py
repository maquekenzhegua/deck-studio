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
from unittest import mock
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


class PlanFileTest(unittest.TestCase):
    """A model outline costs minutes; closing the window must not lose it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)

    def _plan(self):
        from deck.plan import Plan, Slide
        return Plan("测试标题", "副标题", [
            Slide("title", "测试标题", note="副标题"),
            Slide("bullets", "两件事", bullets=["一", "二"]),
            Slide("closing", "收尾"),
        ])

    def test_round_trip_keeps_the_title_and_theme(self):
        import tempfile
        from app import server
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "plan.json"
            with mock.patch.object(server, "PLAN_FILE", target):
                server._save_plan(self._plan(), {"label": "模型主导"}, "editorial")
                plan, planner, theme = server._read_plan([], [])
        self.assertEqual(plan.deck_title, "测试标题")
        self.assertEqual(theme, "editorial")
        self.assertEqual(planner["label"], "模型主导")

    def test_a_corrupt_file_is_ignored_not_fatal(self):
        import tempfile
        from app import server
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "plan.json"
            target.write_text("{ not json", encoding="utf-8")
            with mock.patch.object(server, "PLAN_FILE", target):
                plan, planner, theme = server._read_plan([], [])
        self.assertIsNone(plan)
        self.assertEqual((planner, theme), ({}, ""))

    def test_the_endpoint_writes_the_file_after_an_edit(self):
        import tempfile
        from app import server
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "plan.json"
            with mock.patch.object(server, "PLAN_FILE", target):
                self.client.post("/api/sample")
                self.client.post("/api/edit", json={"text": "第 1 页标题改成：先讲结论"})
                self.assertTrue(target.exists())
                saved = target.read_text(encoding="utf-8")
        self.assertIn("先讲结论", saved)


if __name__ == "__main__":
    unittest.main()


class PreviewDensityTest(unittest.TestCase):
    """Fifteen 16:9 pages at two per row is a scroll, not a review."""

    def test_the_page_honours_a_column_preference(self) -> None:
        root = Path(__file__).resolve().parent.parent
        html = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="cols"', html)
        self.assertIn("deck.cols", html)
        css = (root / "app" / "static" / "theme-light.css").read_text(encoding="utf-8")
        self.assertIn("var(--cols,2)", css)


class NotesRouteTest(unittest.TestCase):
    """No model configured is a 409 with a sentence a person can act on."""

    def test_notes_need_a_model(self):
        from app import server as srv
        c = TestClient(srv.app)
        c.post("/api/sample")
        with mock.patch.object(srv, "client_for_model", return_value=None):
            r = c.post("/api/notes")
        self.assertEqual(r.status_code, 409)
        self.assertIn("模型", r.json()["detail"])


class AutoPipelineTest(unittest.TestCase):
    """One click must end in a deck, not in a spinner.

    The model is mocked out here: what is under test is the wiring, the order
    of the steps, and the fact that a background thread reports progress
    instead of leaving the UI waiting forever.
    """

    def test_auto_runs_to_a_plan_and_reports_steps(self):
        import time
        from app import server as srv
        c = TestClient(srv.app)
        c.post("/api/sample")
        with mock.patch.object(srv, "client_for_model", return_value=None):
            r = c.post("/api/auto", json={"theme": "editorial"})
            self.assertEqual(r.status_code, 200)
            for _ in range(120):
                p = c.get("/api/progress").json()
                if not p["running"]:
                    break
                time.sleep(0.25)
        self.assertFalse(p["running"])
        self.assertEqual(p["error"], "")
        self.assertTrue(p["done"])
        self.assertGreaterEqual(len(p["log"]), 3)
        self.assertIn("在排版", " ".join(p["log"]))
        st = c.get("/api/state").json()
        self.assertGreaterEqual(len(st["plan"]["slides"]), 5)

    def test_only_one_button_writes_notes(self):
        root = Path(__file__).resolve().parent.parent
        html = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('id="notesBtn"'), 1)
        self.assertIn('id="autoBtn"', html)

"""Settings are a product feature, not an env-var footnote.

A user who double-clicks an .exe has no shell to export `DECK_API_KEY` in, so
the app has to own this: write it next to the decks, mirror it into the process
environment, and never show the key back in full.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app import settings

TMP = Path(tempfile.mkdtemp(prefix="deck-settings-"))


class SettingsTest(unittest.TestCase):
    def tearDown(self) -> None:
        for name in ("DECK_BASE_URL", "DECK_API_KEY", "DECK_MODEL"):
            os.environ.pop(name, None)

    def test_round_trip_keeps_values_and_trims(self) -> None:
        path = TMP / "a.json"
        settings.save({"base_url": " https://api.openai.com/v1 ", "model": "gpt-4o-mini",
                       "api_key": "sk-abcdefghijklmnop"}, path=path)
        loaded = settings.load(path)
        self.assertEqual(loaded["base_url"], "https://api.openai.com/v1")
        self.assertEqual(loaded["model"], "gpt-4o-mini")
        self.assertTrue(path.exists())

    def test_bare_host_gets_the_v1_suffix(self) -> None:
        clean = settings._clean({"base_url": "http://10.0.0.5:8000"})
        self.assertEqual(clean["base_url"], "http://10.0.0.5:8000/v1")

    def test_key_is_masked_in_public_view(self) -> None:
        data = settings._clean({"base_url": "https://api.deepseek.com/v1",
                                "api_key": "sk-1234567890abcdef", "model": "deepseek-chat"})
        public = settings.public(data)
        self.assertEqual(public["mode"], "model")
        self.assertNotIn("1234567890", public["text"]["key"])
        self.assertTrue(public["text"]["has_key"])
        self.assertTrue(public["text"]["key"].startswith("sk-1"))

    def test_offline_when_nothing_is_configured(self) -> None:
        public = settings.public({})
        self.assertEqual(public["mode"], "offline")
        self.assertFalse(public["vision_on"])

    def test_apply_mirrors_into_environment(self) -> None:
        settings.apply({"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k",
                        "api_key": "sk-test"})
        self.assertEqual(os.environ.get("DECK_BASE_URL"), "https://api.moonshot.cn/v1")
        self.assertEqual(os.environ.get("DECK_MODEL"), "moonshot-v1-8k")
        # vision stays off unless a vision model was named: a text-only model
        # answering an image request is a 404 dressed up as a feature
        self.assertFalse(os.environ.get("DECK_VISION_MODEL"))

    def test_probe_reports_a_dead_endpoint_without_raising(self) -> None:
        ok, message = settings.probe("http://127.0.0.1:9/v1", "sk-x", "any", timeout=1.5)
        self.assertFalse(ok)
        self.assertTrue(message)

    def test_probe_needs_a_model(self) -> None:
        ok, message = settings.probe("https://api.openai.com/v1", "sk-x", "")
        self.assertFalse(ok)
        self.assertIn("模型", message)

class BaseUrlTest(unittest.TestCase):
    """Every domestic provider versions its path differently; do not guess twice."""

    def test_a_bare_host_gets_v1(self):
        self.assertEqual(settings._clean({"base_url": "https://api.deepseek.com"})["base_url"],
                         "https://api.deepseek.com/v1")

    def test_an_already_versioned_path_is_kept(self):
        for url in ("https://ark.cn-beijing.volces.com/api/v3",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "http://127.0.0.1:11434/v1"):
            self.assertEqual(settings._clean({"base_url": url})["base_url"], url)

    def test_trailing_slash_is_dropped(self):
        self.assertEqual(settings._clean({"base_url": "https://api.moonshot.cn/v1/"})["base_url"],
                         "https://api.moonshot.cn/v1")

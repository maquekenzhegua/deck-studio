"""Settings are a product feature, not an env-var footnote.

A user who double-clicks an .exe has no shell to export `DECK_API_KEY` in, so
the app has to own this: write it next to the decks, mirror it into the process
environment, and never show the key back in full.
"""

from __future__ import annotations

from unittest import mock

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


class PresetCoverageTest(unittest.TestCase):
    """The README promises these providers; the preset list has to keep them.

    A missing preset is not a cosmetic bug: a user with only a Baichuan key has
    no way to type the endpoint from memory, so the app looks broken to them.
    """

    REQUIRED = ("opencode", "deepseek", "dashscope", "zhipu", "moonshot", "doubao",
                "baichuan", "minimax", "siliconflow", "ollama")

    def test_every_named_provider_has_a_usable_preset(self) -> None:
        for key in self.REQUIRED:
            with self.subTest(provider=key):
                self.assertIn(key, settings.PRESETS)
                preset = settings.PRESETS[key]
                self.assertTrue(preset["base_url"].startswith("http"))
                self.assertTrue(preset["model"])
                self.assertTrue(preset["note"])

    def test_preset_urls_are_not_double_suffixed(self) -> None:
        for key, preset in settings.PRESETS.items():
            with self.subTest(provider=key):
                clean = settings._clean({"base_url": preset["base_url"]})
                self.assertEqual(clean["base_url"], preset["base_url"])


class ProbeHeaderTest(unittest.TestCase):
    """OpenCode Go routes on a session header and sits behind a WAF that
    rejects urllib's default signature. The probe button has to speak the same
    dialect as the real client or it reports a false 403."""

    def test_probe_sends_session_and_user_agent(self) -> None:
        seen: dict[str, str] = {}

        class Fake:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"model": "mimo-v2.6-flash"}'

        def fake_urlopen(req, timeout=0):
            seen.update({k.lower(): v for k, v in req.header_items()})
            return Fake()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            ok, message = settings.probe("https://opencode.ai/inference/go/v1", "oc_sk_test", "mimo-v2.6-flash")
        self.assertTrue(ok, message)
        self.assertTrue(seen.get("x-opencode-session", "").startswith("probe-"))
        self.assertIn("deck", seen.get("user-agent", "").lower())

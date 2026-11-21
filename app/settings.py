"""Model settings that live next to the user's decks, not in their shell.

The tool works with no model at all — `compose.py` is the offline planner and
`multimodal.py` classifies pictures geometrically. A model only adds two
things: better prose, and actually reading what a picture means.

Both are optional, so the settings have to be optional too, and they have to
survive a double-click: a user who launches the .exe has no terminal to export
`DECK_API_KEY` in. So the app writes them to one JSON file inside its own
workspace and mirrors them into the environment for the rest of the process.
"""

from __future__ import annotations

import re

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import paths

FILENAME = "settings.json"

PRESETS: dict[str, dict[str, str]] = {
    # Domestic endpoints first: that is what most users of this tool can
    # actually buy, and the ones whose free tiers make a first run possible.
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-chat", "vision_model": "",
                 "note": "便宜、稳；只有文本，不能读图"},
    "dashscope": {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "model": "qwen-plus", "vision_model": "qwen-vl-max",
                  "note": "文本与视觉都有，视觉用 qwen-vl-max"},
    "zhipu": {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4",
              "model": "glm-4-flash", "vision_model": "glm-4v-flash",
              "note": "glm-4-flash 免费额度大；视觉 glm-4v-flash"},
    "moonshot": {"label": "Kimi / Moonshot", "base_url": "https://api.moonshot.cn/v1",
                 "model": "moonshot-v1-8k", "vision_model": "moonshot-v1-8k-vision-preview",
                 "note": "长上下文；视觉需单独开通"},
    "doubao": {"label": "豆包 / 火山方舟", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
               "model": "doubao-pro-32k", "vision_model": "",
               "note": "模型名填方舟里的接入点 ID"},
    "siliconflow": {"label": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
                    "model": "Qwen/Qwen2.5-72B-Instruct",
                    "vision_model": "Qwen/Qwen2-VL-72B-Instruct",
                    "note": "一个 key 通吃开源模型，视觉可选 Qwen2-VL"},
    "baichuan": {"label": "百川智能", "base_url": "https://api.baichuan-ai.com/v1",
                 "model": "Baichuan4-Air", "vision_model": "",
                 "note": "文本；模型名可换成 Baichuan4"},
    "minimax": {"label": "MiniMax", "base_url": "https://api.minimax.chat/v1",
                "model": "abab6.5s-chat", "vision_model": "",
                "note": "文本为主"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1",
               "model": "gpt-4o-mini", "vision_model": "gpt-4o-mini",
               "note": "文本与视觉同模型"},
    "ollama": {"label": "本机 Ollama", "base_url": "http://127.0.0.1:11434/v1",
               "model": "qwen2.5:7b", "vision_model": "qwen2.5vl:7b",
               "note": "离线；先 ollama pull 对应模型"},
}

FIELDS = ("base_url", "api_key", "model", "vision_url", "vision_key", "vision_model")


def file_path() -> Path:
    return paths.home() / FILENAME


def _clean(data: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in FIELDS:
        value = (data or {}).get(key, "")
        out[key] = str(value or "").strip()
    # A bare host is a common paste; the client always appends
    # /chat/completions. Only add a version segment when the URL has none:
    # 豆包 is /api/v3 and DashScope is /compatible-mode/v1, and appending a
    # second version to either produces a 404 the user cannot diagnose.
    for key in ("base_url", "vision_url"):
        url = out[key].rstrip("/")
        if url and not re.search(r"/v\d+$", url):
            out[key] = url + "/v1"
        elif url:
            out[key] = url
    return out


def load(path: Path | None = None) -> dict[str, str]:
    target = path or file_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return _clean(data)


def save(data: dict, path: Path | None = None) -> dict[str, str]:
    clean = _clean(data)
    target = path or file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    apply(clean)
    return clean


def apply(data: dict | None = None) -> dict[str, str]:
    """Mirror the file into the environment the rest of the code reads.

    With explicit data (the UI saving a form) an empty field means "clear it".
    Without data (startup, reading the file) an empty field must leave the
    environment alone: DECK_* set by a deployment or CI is a legitimate
    configuration, and wiping it at boot made env-only setups silently fall
    back to offline planning.
    """
    explicit = data is not None
    clean = _clean(data) if data is not None else load()
    pairs = {
        "DECK_BASE_URL": clean["base_url"],
        "DECK_API_KEY": clean["api_key"],
        "DECK_MODEL": clean["model"],
        "DECK_VISION_URL": clean["vision_url"] or clean["base_url"],
        "DECK_VISION_KEY": clean["vision_key"] or clean["api_key"],
        "DECK_VISION_MODEL": clean["vision_model"],
    }
    for name, value in pairs.items():
        if value:
            os.environ[name] = value
        elif explicit and name in os.environ:
            os.environ.pop(name, None)
    return clean


def mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:4]}••••{secret[-4:]}"


def public(data: dict | None = None) -> dict:
    clean = _clean(data) if data is not None else load()
    # A deployment can configure the endpoint purely through the environment
    # (Docker, CI, a shortcut with env vars). Reporting "offline" while the
    # model is in fact driving would mislabel every deck it writes.
    env_on = bool(os.environ.get("DECK_BASE_URL") and os.environ.get("DECK_MODEL")
                  and os.environ.get("DECK_API_KEY"))
    text_on = bool(clean["base_url"] and clean["model"]) or env_on
    # A vision model is a separate choice: pointing it at a text-only model
    # (DeepSeek, MiniMax) fails at the first image with a 404, so absence of an
    # explicit vision model means "off", not "reuse the text one".
    # An environment-only deployment can configure the eyes too; report it the
    # same way as it reports the text model, or the Look button stays hidden on
    # a machine that could actually use it.
    env_vision = bool(os.environ.get("DECK_VISION_MODEL")
                      and (os.environ.get("DECK_VISION_URL") or os.environ.get("DECK_BASE_URL"))
                      and (os.environ.get("DECK_VISION_KEY") or os.environ.get("DECK_API_KEY")))
    vision_on = (bool(clean["vision_model"]) and bool(
        clean["vision_url"] or clean["base_url"]
    )) or env_vision
    return {
        "text": {
            "base_url": clean["base_url"],
            "model": clean["model"],
            "key": mask(clean["api_key"]),
            "has_key": bool(clean["api_key"]),
        },
        "vision": {
            "base_url": clean["vision_url"],
            "model": clean["vision_model"] or os.environ.get("DECK_VISION_MODEL", ""),
            "key": mask(clean["vision_key"]),
            "has_key": bool(clean["vision_key"]),
        },
        "mode": "model" if text_on else "offline",
        "source": "file" if (clean["base_url"] and clean["model"]) else ("env" if env_on else ""),
        "env_model": os.environ.get("DECK_MODEL", ""),
        "vision_on": vision_on,
        "presets": [{"id": k, **v} for k, v in PRESETS.items()],
    }


def probe(base_url: str, api_key: str, model: str, timeout: float = 12.0) -> tuple[bool, str]:
    """One minimal chat call. Cheap enough to press a button, honest enough
    to catch a wrong model name or a key that has no quota."""
    base = (base_url or "").rstrip("/")
    if not base or not model:
        return False, "先填接口地址和模型名"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}" if api_key else "",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
        got = (body.get("model") or model)
        return True, f"连通 · {got}"
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
            detail = (body.get("error") or {}).get("message") or raw
        except ValueError:
            detail = raw
        detail = " ".join(str(detail).split())[:110]
        return False, f"HTTP {exc.code} · {detail or exc.reason}"
    except Exception as exc:  # noqa: BLE001 - network shapes vary by vendor
        return False, f"{type(exc).__name__} · {exc}"

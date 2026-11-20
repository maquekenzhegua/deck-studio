"""Optional visual understanding, off by default.

Classification and colour extraction (multimodal.py) work offline and are
enough to decide *how* a picture should be placed. They cannot tell you what
the picture means. That needs a vision model, and a local deck tool must not
require one: this module is a thin, optional client for any OpenAI-compatible
endpoint.

    DECK_VISION_URL=https://api.openai.com/v1
    DECK_VISION_KEY=...
    DECK_VISION_MODEL=gpt-4o-mini

Unset means `available()` is False and every call returns None, so the rest of
the pipeline behaves exactly as before.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

PROMPT = (
    "你在帮一份中文商业汇报挑图。用不超过 30 个汉字说明这张图展示了什么，"
    "以及它在汇报里能支持什么判断。只回答一句话，不要客套。"
)


def config() -> dict[str, str]:
    return {
        "url": os.environ.get("DECK_VISION_URL", "").rstrip("/"),
        "key": os.environ.get("DECK_VISION_KEY", ""),
        "model": os.environ.get("DECK_VISION_MODEL", ""),
    }


def available() -> bool:
    c = config()
    return bool(c["url"] and c["model"])


def _mime(path: Path) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower(), "image/png")


def describe(path: str | Path, timeout: float = 40.0) -> str | None:
    """One sentence about the picture, or None when unavailable/failed."""
    if not available():
        return None
    p = Path(path)
    if not p.exists() or p.stat().st_size > 8 * 1024 * 1024:
        return None
    c = config()
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    body = {
        "model": c["model"],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{_mime(p)};base64,{data}"}},
            ],
        }],
        "max_tokens": 120,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        c["url"] + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {c['key']}"} if c["key"] else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - operator-set endpoint
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001 - a vision call must never break a build
        return None
    text = " ".join(str(text).split())
    return text[:120] or None

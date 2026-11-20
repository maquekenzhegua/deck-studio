"""Model client: any OpenAI-compatible endpoint, no vendor SDK.

Moat #3 starts here. A deck tool that only talks to one vendor cannot be
deployed inside a company that runs its own vLLM behind the firewall. So the
transport is plain HTTPS + JSON, configured entirely by environment variables:

    DECK_BASE_URL   e.g. http://10.0.0.5:8000/v1
    DECK_API_KEY
    DECK_MODEL

Nothing in this file assumes the endpoint is a cloud service.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

DEFAULT_BASE = "https://opencode.ai/inference/go/v1"
DEFAULT_MODEL = "mimo-v2.6-flash"

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMError(RuntimeError):
    pass


def _api_key() -> str:
    for name in ("DECK_API_KEY", "OPENCODE_GO_API_KEY", "LOOM_API_KEY"):
        v = os.environ.get(name)
        if v:
            return v
    # Last resort: the DSH credential store, which is how the rest of this
    # machine holds the key.
    p = os.path.expanduser("~/.dsh/.credentials.yaml")
    if os.path.exists(p):
        import re

        text = open(p, encoding="utf-8", errors="ignore").read()
        m = re.search(r"OPENCODE_GO_API_KEY\s*:\s*['\"]?([A-Za-z0-9_\-]+)", text)
        if m:
            return m.group(1)
    raise LLMError("没有 API key：设置 DECK_API_KEY，或把任意 OpenAI 兼容服务的 key 放进环境变量")


class Client:
    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None,
                 timeout: int = 180, attempts: int = 4):
        self.base = (base_url or os.environ.get("DECK_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.model = model or os.environ.get("DECK_MODEL") or DEFAULT_MODEL
        self.key = api_key or _api_key()
        self.timeout = timeout
        self.attempts = attempts
        self.session = os.environ.get("DECK_SESSION") or str(uuid.uuid4())
        self.usage = Usage()

    # -- transport ---------------------------------------------------------
    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        last = ""
        for i in range(self.attempts):
            req = urllib.request.Request(
                f"{self.base}{path}",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.key}",
                    # Some gateways sit behind a WAF that rejects urllib's
                    # default signature outright (Cloudflare 1010).
                    "User-Agent": "deck/0.1 (+https://github.com/)",
                    "Accept": "application/json",
                    # opencode's go gateway wants a session id for routing.
                    # Harmless on any other OpenAI-compatible endpoint.
                    "x-opencode-session": self.session,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")[:400]
                last = f"HTTP {e.code}: {detail}"
                if e.code not in RETRYABLE or i == self.attempts - 1:
                    raise LLMError(f"{self.model} {last}") from None
                time.sleep(min(2 ** i, 20) + random.random())
            except (urllib.error.URLError, TimeoutError) as e:
                last = str(e)
                if i == self.attempts - 1:
                    raise LLMError(f"{self.model} 连接失败: {last}") from None
                time.sleep(min(2 ** i, 20) + random.random())
        raise LLMError(last)

    # -- chat --------------------------------------------------------------
    def chat(self, system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 4096) -> str:
        # A reasoning model spends completion tokens on thinking before it
        # writes anything; a 2.6k budget can be consumed entirely by that and
        # return finish_reason=length with an empty message. Start higher and
        # grow on the empty retry instead of treating it as a broken endpoint.
        max_tokens = max(int(max_tokens), int(os.environ.get("DECK_MAX_TOKENS") or 0), 4096)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Cheap models occasionally return an empty message (truncated by the
        # token cap, or reasoning-only). That is a transport hiccup, not an
        # answer, so retry before giving up.
        for attempt in range(3):
            data = self._post("/chat/completions", payload)
            try:
                choice = data["choices"][0]
                msg = (choice.get("message") or {}).get("content") or ""
                if not msg.strip():
                    msg = (choice.get("message") or {}).get("reasoning_content") or ""
            except (KeyError, IndexError):
                raise LLMError(f"响应格式不认识: {json.dumps(data)[:300]}") from None
            u = data.get("usage") or {}
            self.usage.prompt_tokens += int(u.get("prompt_tokens") or 0)
            self.usage.completion_tokens += int(u.get("completion_tokens") or 0)
            if msg.strip():
                return msg
            payload["max_tokens"] = min(int(payload["max_tokens"] * 1.8), 16000)
            time.sleep(1.0 + attempt)
        raise LLMError(f"{self.model} 连续 3 次返回空内容（finish_reason={data.get('choices', [{}])[0].get('finish_reason')}）")

    def vision(self, system: str, text: str, images, *, temperature: float = 0.2,
               max_tokens: int = 1200) -> str:
        """Ask a vision model about pictures.

        The image goes in as a data URL, which every OpenAI-compatible gateway
        accepts without needing an upload endpoint. A text-only model answers
        404 here — that is why the vision model is a separate setting.
        """
        parts = [{"type": "text", "text": text}]
        for path in images:
            mime, blob = _b64(path)
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{blob}"}})
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": parts},
            ],
            "temperature": temperature,
            "max_tokens": max(int(max_tokens), int(os.environ.get("DECK_MAX_TOKENS") or 0)),
        }
        data = self._post("/chat/completions", payload)
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError) as exc:
            raise LLMError(f"返回格式不对：{exc}") from exc

    def json(self, system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 4096) -> dict:
        """Ask for JSON, and survive models that wrap it in prose or fences.

        Small models break JSON in ways no regex fixes: a missing comma between
        keys, an unescaped quote in a Chinese sentence. Rather than guess, hand
        the model its own broken output back with the parser's complaint. One
        repair round fixes almost everything and costs a fraction of a cent.
        """
        raw = self.chat(system, user, temperature=temperature, max_tokens=max_tokens)
        try:
            return parse_json(raw)
        except (LLMError, json.JSONDecodeError) as e:
            fix_system = "你上一条输出不是合法 JSON。只输出修正后的 JSON，不要任何解释、不要 markdown 代码块。"
            fix_user = f"解析错误：{e}\n\n你之前的输出：\n{raw[:4000]}\n\n请输出修正后的合法 JSON。"
            raw2 = self.chat(fix_system, fix_user, temperature=0.0, max_tokens=max_tokens)
            return parse_json(raw2)


def parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        chunk = text[start : end + 1]
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            # Trailing commas are the most common failure from small models.
            import re

            fixed = re.sub(r",\s*([}\]])", r"\1", chunk)
            return json.loads(fixed)
    raise LLMError(f"模型没给出 JSON: {raw[:300]}")


def _b64(path) -> tuple[str, str]:
    """A local image as (mime, base64). Downscaling is the caller's job."""
    import base64
    import mimetypes

    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return mime, base64.b64encode(open(path, "rb").read()).decode("ascii")

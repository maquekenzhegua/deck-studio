"""Acceptance: a fresh install, files dropped in through the API, a deck out.

This is the story a person gets after unzipping the release: no Python, no
Office, no model key. If this passes, the download is usable.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = ROOT / "out" / "acceptance"
def _newest_build() -> Path:
    """The build a person would click: the most recent one, not a pinned path."""
    builds = sorted(ROOT.glob("dist*/Deck Studio/Deck Studio.exe"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    return builds[0] if builds else ROOT / "dist" / "Deck Studio" / "Deck Studio.exe"


EXE = Path(os.environ.get("DECK_EXE") or _newest_build())
SAMPLES = ROOT / "samples" / "data"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def post(url: str, body: dict):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read().decode("utf-8"))


def post_files(url: str, paths: list[Path]):
    boundary = "----deck" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for path in paths:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode()
        )
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir(parents=True)
    port = free_port()
    env = dict(os.environ, DECK_HEADLESS="1", DECK_PORT=str(port), DECK_HOME=str(HOME))
    if not EXE.exists():
        print("no packaged build at", EXE, "- set DECK_EXE or build first")
        return 2
    proc = subprocess.Popen([str(EXE.resolve())], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    checks: list[tuple[str, bool, str]] = []
    try:
        for _ in range(100):
            try:
                get(base + "/health")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)

        files = sorted(p for p in SAMPLES.iterdir() if p.is_file())
        up = post_files(base + "/api/upload", files)
        state = get(base + "/api/state")
        checks.append(("上传全部示例文件", len(state["files"]) == len(files),
                       f"{len(state['files'])}/{len(files)} · skipped={up.get('skipped')}"))
        checks.append(("抽出事实", len(state["facts"]) >= 15, f"{len(state['facts'])} 条"))
        checks.append(("资料体检", state["readiness"]["score"] >= 4,
                       f"{state['readiness']['summary']}"))
        checks.append(("识别图片", len([p for p in state["pictures"] if p["kind"]]) >= 2,
                       f"{len(state['pictures'])} 张"))

        built = post(base + "/api/build",
                     {"plan": state["plan"], "theme": "editorial", "force": True})
        checks.append(("生成 16 页", len(built["pages"]) == 16, f"{len(built['pages'])} 页"))
        checks.append(("质量分", (built.get("qa") or {}).get("score", 0) >= 90,
                       str((built.get("qa") or {}).get("score"))))
        checks.append(("预览导出", not built.get("previewError"),
                       built.get("previewError") or "ok"))
        checks.append(("数字校验", not built["problems"], str(len(built["problems"]))))

        exported = post(base + "/api/export", {"target": "pdf"})
        pdf = Path(exported.get("pdf") or "")
        checks.append(("导出 PDF", pdf.exists() and pdf.stat().st_size > 100_000,
                       f"{pdf.name} · {pdf.stat().st_size // 1024} KB" if pdf.exists() else "missing"))
        # Leave no litter on the desktop.
        for item in (pdf, Path(exported.get("path") or "")):
            try:
                item.unlink()
            except OSError:
                pass

        ok = all(c[1] for c in checks)
        for name, passed, detail in checks:
            print(("PASS " if passed else "FAIL ") + name + " · " + detail)
        print("ACCEPTANCE", "OK" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())

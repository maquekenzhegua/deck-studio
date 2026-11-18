"""Collect a corpus of real decks to measure against.

The design constants in deck/theme.py were picked by reading about deck design.
This script exists so they can be picked by measuring decks instead.

Two things make this less trivial than a for-loop over URLs:
  * templates run 2-42MB (the big ones are bundled photo packs), and a plain
    urlopen().read() sits on a 42MB body until the socket times out;
  * the CDN throttles per connection, so throughput comes from concurrency.
So: HEAD for the size, skip anything oversized, stream the rest with a hard
byte cap and a wall-clock deadline, six at a time.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
BASE = "https://www.slidescarnival.com"
MAX_MB = 3
MAX_BYTES = MAX_MB * 1024 * 1024
DEADLINE_S = 75
WORKERS = 10

# Template galleries are full of school projects and birthday cards. These are
# the words that mark a deck someone would present at work.
WANT = re.compile(
    r"business|consult|corporat|report|strateg|market|pitch|financ|annual|"
    r"proposal|sales|growth|company|startup|profession|elegant|luxury|minimal|"
    r"agency|brand|roadmap|kpi|dashboard|quarter|budget|project|meeting|"
    r"analytics|insight|planning|okr|deck",
    re.I,
)


def _req(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=UA)


def listing(page: int) -> list[tuple[str, str]]:
    url = f"{BASE}/category/free-templates/powerpoint-templates/page/{page}"
    try:
        with urllib.request.urlopen(_req(url), timeout=40) as r:
            html = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    return sorted(set(re.findall(r"/download/([a-z0-9]+)/([a-z0-9\-]+)/pptx", html)))


def head_size(url: str) -> int | None:
    with urllib.request.urlopen(_req(url), timeout=25) as r:
        n = r.headers.get("Content-Length")
        return int(n) if n else None


def stream(url: str, dest: Path) -> str | None:
    """Download with a byte cap and a wall-clock deadline. None means success."""
    started = time.time()
    try:
        with urllib.request.urlopen(_req(url), timeout=30) as r:
            buf = bytearray()
            while True:
                if time.time() - started > DEADLINE_S:
                    return f"stalled at {len(buf)//1024}KB"
                chunk = r.read(262144)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_BYTES:
                    return f"over {MAX_MB}MB"
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}"
    if not buf.startswith(b"PK"):
        return f"not-a-zip({len(buf)}B)"
    dest.write_bytes(bytes(buf))
    return None


def fetch_one(item: tuple[str, str]) -> tuple[str, str, str | None]:
    tid, slug = item
    dest = RAW / f"{tid}.pptx"
    if dest.exists() and dest.stat().st_size > 20000:
        return tid, slug, None
    url = f"{BASE}/download/{tid}/{slug}/pptx"
    try:
        size = head_size(url)
    except Exception as e:  # noqa: BLE001
        return tid, slug, f"head {type(e).__name__}"
    if size is not None and size > MAX_BYTES:
        return tid, slug, f"skip {size // 1024 // 1024}MB"
    return tid, slug, stream(url, dest)


def main(limit: int = 60, pages: int = 24) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    for p in range(1, pages + 1):
        try:
            found = listing(p)
        except Exception as e:  # noqa: BLE001
            print(f"  page {p}: {type(e).__name__}", flush=True)
            continue
        if not found:
            print(f"  page {p}: end of listing", flush=True)
            break
        for tid, slug in found:
            if WANT.search(slug):
                seen.setdefault(tid, slug)
        print(f"  page {p}: {len(found)} links, {len(seen)} kept", flush=True)

    keep = list(seen.items())
    print(f"\n{len(keep)} candidates, grabbing {limit}", flush=True)
    ok: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_one, it) for it in keep]
        for n, fut in enumerate(as_completed(futures), 1):
            tid, slug, err = fut.result()
            if err:
                print(f"  [{n}] {tid} {err}", flush=True)
            else:
                ok.append({"id": tid, "slug": slug, "file": f"{tid}.pptx"})
                kb = (RAW / f"{tid}.pptx").stat().st_size // 1024
                print(f"  [{n}] {tid} OK {kb}KB {slug}", flush=True)
            if len(ok) >= limit:
                for f2 in futures:
                    f2.cancel()
                break

    (ROOT / "index.json").write_text(
        json.dumps(ok, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\ndone: {len(ok)} decks in {RAW}", flush=True)


if __name__ == "__main__":
    main(
        limit=int(sys.argv[1]) if len(sys.argv) > 1 else 60,
        pages=int(sys.argv[2]) if len(sys.argv) > 2 else 24,
    )

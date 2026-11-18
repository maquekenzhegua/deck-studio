"""Download the small half of the candidate list.

Measured throughput to the template CDN is ~0.03MB/s per connection, and decks
run 2-300MB because each bundles a photo pack. At that rate "download all the
good decks" is a day of waiting for a corpus that only needs to be statistical.

So: HEAD everything first, then pull the smallest decks with many workers. The
small ones are also the text-heavy business decks rather than the wallpaper-led
ones, which is the population we actually want to measure.
"""
from __future__ import annotations

import json
import sys
import time
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
MAX_BYTES = 14 * 1024 * 1024
DEADLINE_S = 200


def head(item: tuple[str, str]) -> tuple[str, str, int]:
    tid, slug = item
    url = f"{BASE}/download/{tid}/{slug}/pptx"
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=20) as r:
            return tid, slug, int(r.headers.get("Content-Length") or 0)
    except Exception:  # noqa: BLE001
        return tid, slug, -1


def grab(item: tuple[str, str]) -> tuple[str, str, str | None]:
    tid, slug = item
    dest = RAW / f"{tid}.pptx"
    if dest.exists() and dest.stat().st_size > 20000:
        return tid, slug, None
    url = f"{BASE}/download/{tid}/{slug}/pptx"
    started = time.time()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=30
        ) as r:
            buf = bytearray()
            while True:
                if time.time() - started > DEADLINE_S:
                    return tid, slug, "stalled"
                chunk = r.read(131072)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_BYTES:
                    return tid, slug, "big"
    except Exception as e:  # noqa: BLE001
        return tid, slug, type(e).__name__
    if not buf.startswith(b"PK"):
        return tid, slug, "not-zip"
    dest.write_bytes(bytes(buf))
    return tid, slug, None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cands = json.loads((ROOT / "candidates.json").read_text(encoding="utf-8"))
    items = list(cands.items())
    have = {p.stem for p in RAW.glob("*.pptx")}
    items = [(t, s) for t, s in items if t not in have]
    print(f"{len(items)} candidates, {len(have)} already here", flush=True)

    sizes: list[tuple[str, str, int]] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for fut in as_completed([pool.submit(head, it) for it in items]):
            tid, slug, n = fut.result()
            if 0 < n <= MAX_BYTES:
                sizes.append((tid, slug, n))
    sizes.sort(key=lambda x: x[2])
    print(f"{len(sizes)} under {MAX_BYTES//1024//1024}MB; smallest first:", flush=True)
    for tid, slug, n in sizes[:8]:
        print(f"  {tid} {n//1024}KB {slug}", flush=True)

    got = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        for n, fut in enumerate(as_completed([pool.submit(grab, (t, s)) for t, s, _ in sizes]), 1):
            tid, slug, err = fut.result()
            if err:
                if n % 10 == 0:
                    print(f"  ...{n}/{len(sizes)} ({err})", flush=True)
                continue
            got += 1
            print(f"  [{got}] {tid} {slug}", flush=True)
    print(f"\ndone: +{got} decks", flush=True)


if __name__ == "__main__":
    main()

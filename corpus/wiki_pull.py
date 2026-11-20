"""Pull reference decks from slides.wiki.

The point is not to collect pretty pictures. It is to have a corpus of real
Fortune-100 decks that we can (a) look at, and (b) measure, so that "好看"
stops being an opinion and becomes a set of numbers we can render against.

Usage:
    python corpus/wiki_pull.py            # thumbnails for the seed list
    python corpus/wiki_pull.py --pdf      # also pull the source PDFs

Thumbnails are ~15KB each and are enough for layout study. PDFs are 2-10MB
and give us vector text + exact colours, which is what `corpus/analyze_refs.py`
uses.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "refs"
BLOB = "https://w6jatzabafyrywse.public.blob.vercel-storage.com"

# slug: page count from the gallery listing
DECKS = {
    "chevron": 47,
    "caterpillar": 101,
    "cvs-health": 102,
    "walmart": 183,
    "jpmorgan-chase": 153,
    "exxonmobil": 42,
    "goldman-sachs": 9,
    "general-motors": 59,
    "ups": 88,
    "delta-air-lines": 55,
    "fedex": 54,
    "pepsico": 35,
    "coca-cola": 47,
    "home-depot": 72,
    "intel": 16,
    "ibm": 16,
    "nike": 60,
    "microsoft": 60,
    "tesla": 40,
}


def get(url: str, dest: Path, tries: int = 4, timeout: int = 45) -> bool:
    if dest.exists() and dest.stat().st_size > 1024:
        return True
    for i in range(tries):
        try:
            r = requests.get(url, timeout=timeout, stream=True)
            if r.status_code != 200:
                if r.status_code == 404:
                    return False
                raise RuntimeError(f"HTTP {r.status_code}")
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    if chunk:
                        fh.write(chunk)
            tmp.replace(dest)
            return True
        except Exception as exc:  # noqa: BLE001 - network is allowed to be bad
            if i == tries - 1:
                print(f"  ! {dest.name}: {type(exc).__name__}", file=sys.stderr)
                return False
            time.sleep(1.5 * (i + 1))
    return False


def main() -> int:
    want_pdf = "--pdf" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    jobs: list[tuple[str, int, Path]] = []
    for slug, pages in DECKS.items():
        d = OUT / slug
        d.mkdir(exist_ok=True)
        for n in range(1, pages + 1):
            jobs.append((slug, n, d / f"{n:03d}.webp"))

    results: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = {
            pool.submit(get, f"{BLOB}/thumbs/{slug}/{n}.webp", dest): (slug, n)
            for slug, n, dest in jobs
        }
        for fut in as_completed(futs):
            slug, _ = futs[fut]
            results[slug] = results.get(slug, 0) + (1 if fut.result() else 0)
    for slug, pages in DECKS.items():
        got = results.get(slug, 0)
        print(f"{slug:16s} {got:3d}/{pages} thumbs")
        manifest[slug] = {"pages": pages, "thumbs": got, "pdf": False}

    if want_pdf:
        for slug in DECKS:
            ok = get(f"{BLOB}/decks/{slug}.pdf", OUT / slug / "deck.pdf", timeout=240)
            manifest[slug]["pdf"] = ok
            print(f"{slug:16s} pdf={'yes' if ok else 'no'}")

    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

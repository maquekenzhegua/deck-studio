"""Build contact sheets and measure the reference corpus.

Two outputs:

1. `corpus/refs/sheet-*.png` — contact sheets we can actually look at. A deck
   is a sequence; judging one page in isolation is how you end up with eight
   pretty pages that do not belong together.
2. `corpus/refs/stats.json` — per-slide measurements (ink coverage, dark ratio,
   dominant colours, edge density). These become the target bands the renderer
   is audited against, instead of numbers someone made up.

Run: python corpus/refs_sheet.py
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
REFS = ROOT / "refs"
W, H = 400, 225


def load(path: Path) -> Image.Image:
    im = Image.open(path).convert("RGB")
    return im.resize((W, H), Image.LANCZOS)


def sheet(paths: list[Path], out: Path, cols: int = 4, label: bool = True) -> None:
    rows = (len(paths) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * W, rows * H), (235, 235, 235))
    draw = ImageDraw.Draw(canvas)
    for i, p in enumerate(paths):
        x, y = (i % cols) * W, (i // cols) * H
        try:
            canvas.paste(load(p), (x, y))
        except Exception:
            pass
        if label:
            draw.rectangle([x, y + H - 16, x + 118, y + H], fill=(0, 0, 0))
            draw.text((x + 4, y + H - 14), p.parent.name[:12] + "/" + p.stem, fill=(255, 255, 255))
    canvas.save(out, "PNG")
    print("wrote", out, canvas.size)


def stats(path: Path) -> dict:
    im = Image.open(path).convert("RGB")
    small = im.resize((160, 90))
    px = list(small.getdata())
    n = len(px)
    lum = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in px]
    dark = sum(1 for v in lum if v < 60) / n
    light = sum(1 for v in lum if v > 235) / n
    # ink = pixels that are neither near-white nor near-black background
    ink = sum(1 for v in lum if 70 < v < 232) / n
    # colourfulness: mean max-min channel
    sat = sum(max(r, g, b) - min(r, g, b) for r, g, b in px) / n
    # edge density: mean absolute horizontal difference in luminance
    edges = 0
    for y in range(90):
        row = lum[y * 160:(y + 1) * 160]
        edges += sum(abs(row[i] - row[i - 1]) for i in range(1, 160))
    return {
        "file": str(path.relative_to(REFS)).replace("\\", "/"),
        "deck": path.parent.name,
        "page": int(path.stem),
        "dark": round(dark, 3),
        "light": round(light, 3),
        "ink": round(ink, 3),
        "sat": round(sat, 1),
        "edge": round(edges / (90 * 159), 2),
    }


def main() -> None:
    decks = sorted([d for d in REFS.iterdir() if d.is_dir() and list(d.glob("*.webp"))])
    print("decks:", len(decks))

    # 1. covers, one per deck
    covers = [sorted(d.glob("*.webp"))[0] for d in decks if list(d.glob("*.webp"))]
    sheet(covers, REFS / "sheet-covers.png", cols=5)

    # 2. early pages (title, agenda, divider, first content) from 8 decks
    pick = ["chevron", "walmart", "jpmorgan-chase", "goldman-sachs", "delta-air-lines",
            "pepsico", "home-depot", "caterpillar"]
    early = []
    for slug in pick:
        for n in range(1, 9):
            p = REFS / slug / f"{n:03d}.webp"
            if p.exists():
                early.append(p)
    sheet(early, REFS / "sheet-early.png", cols=6)

    # 3. random sample across the whole corpus, to catch layout families
    allp = [p for d in decks for p in sorted(d.glob("*.webp"))]
    random.seed(7)
    sample = random.sample(allp, min(32, len(allp)))
    sheet(sample, REFS / "sheet-sample.png", cols=6)

    # 4. statistics
    rows = [stats(p) for p in allp]
    (REFS / "stats.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    by_deck = defaultdict(list)
    for r in rows:
        by_deck[r["deck"]].append(r)
    print(f"{'deck':18s} {'dark%':>6s} {'ink%':>6s} {'sat':>5s} {'edge':>5s}")
    for deck, rs in by_deck.items():
        m = {k: sum(x[k] for x in rs) / len(rs) for k in ("dark", "ink", "sat", "edge")}
        print(f"{deck:18s} {m['dark']*100:6.1f} {m['ink']*100:6.1f} {m['sat']:5.1f} {m['edge']:5.1f}")

    counts = Counter("dark" if r["dark"] > 0.55 else "light" for r in rows)
    print("dark/light slides:", dict(counts))
    print("median ink:", sorted(r["ink"] for r in rows)[len(rows) // 2])


if __name__ == "__main__":
    main()

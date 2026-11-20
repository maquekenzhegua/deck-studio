"""Rebuild the picture the README shows.

One command, no model, no network: sample data in, a fifteen-page deck out, a
contact sheet in docs/. If this script cannot reproduce the screenshot, the
screenshot is a lie — so it is the only way the preview image is made.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from deck import compose, compute, ingest, multimodal, render2, verify
from tools.preview import contact_sheet, export_pdf, export_png


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="samples/data")
    ap.add_argument("--theme", default="editorial")
    ap.add_argument("--out", default="out/demo")
    ap.add_argument("--docs", default="docs/deck-preview.png")
    ap.add_argument("--cols", type=int, default=3)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bundle = ingest.load(Path(args.data))
    multimodal.augment(bundle, out / "media", pages=True)
    facts = compute.extract(bundle)
    plan = compose.draft(bundle, facts)
    problems = verify.check(plan, facts)
    if problems:
        for p in problems:
            print(f"[demo] verify: slide {p.slide} · {p.kind} · {p.detail}")
        return 1

    pptx = out / "deck.pptx"
    render2.build(plan, facts, bundle.pictures, str(pptx), theme=args.theme)
    pages = export_png(pptx, out / "png", 1600, 900)
    sheet = Path(args.docs)
    contact_sheet(pages, sheet, cols=args.cols, gap=14)
    try:
        export_pdf(pptx, out / "deck.pdf")
    except Exception as exc:  # noqa: BLE001 - PDF is a bonus, not the point
        print(f"[demo] pdf skipped: {exc}")
    print(f"[demo] {len(plan.slides)} pages · {sheet} ({sheet.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

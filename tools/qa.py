"""Deck quality gate: the checks a human art director would run on a PDF.

Reads the PPTX for structural problems and the exported PNGs for the things
only the renderer knows (ink coverage, empty pages). The score is what the
desktop app shows as a badge — it is a prompt to look, not a certificate.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu

EMU_IN = 914400
BODY_MIN = 10.5
TITLE_MAX = 120
FURNITURE = re.compile(r"^(\d{1,3}\s*/\s*\d{1,3}|\d{2}|元|个|%|经营复盘|读法|下一步|"
                       r"STRATEGY REVIEW|SECTION \d+|来源\s*·.*|\d+\s*页\s*·.*)$")


def _text_shapes(prs):
    for idx, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = "".join(p.text for p in shape.text_frame.paragraphs).strip()
            if not text:
                continue
            sizes = [r.font.size.pt for p in shape.text_frame.paragraphs
                     for r in p.runs if r.font.size]
            yield idx, shape, text, sizes


def _ink(png: Path) -> tuple[float, tuple[int, int, int]] | None:
    """Share of pixels that differ from the page's dominant colour, plus that colour."""
    try:
        from PIL import Image
    except ImportError:                                  # pragma: no cover
        return None
    with Image.open(png) as im:
        small = im.convert("RGB").resize((240, 135))
    px = list(small.getdata())
    bg = Counter(px).most_common(1)[0][0]
    diff = sum(1 for p in px if abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > 34)
    return diff / len(px), bg


def check(pptx: str | Path, png_dir: str | Path | None = None) -> dict:
    pptx = Path(pptx)
    prs = Presentation(str(pptx))
    W, H = prs.slide_width, prs.slide_height
    issues: list[dict] = []
    per_slide: list[dict] = []

    for idx in range(len(prs.slides)):
        per_slide.append({"slide": idx + 1, "shapes": 0, "chars": 0,
                          "ink": None, "issues": []})

    for idx, shape, text, sizes in _text_shapes(prs):
        row = per_slide[idx]
        row["shapes"] += 1
        row["chars"] += len(text)

        if shape.left < -Emu(int(0.02 * EMU_IN)) or shape.top < -Emu(int(0.02 * EMU_IN)):
            msg = f"元素越出页面左上：{text[:14]}"
            row["issues"].append(msg)
            issues.append({"slide": idx + 1, "kind": "bounds", "detail": msg})
        bleeds = bool(re.fullmatch(r"\d{2}", text)) or (sizes and max(sizes) > TITLE_MAX)
        if not bleeds and shape.left + shape.width > W + Emu(int(0.02 * EMU_IN)):
            msg = f"元素越出右边界 {int((shape.left + shape.width - W) / EMU_IN * 100) / 100}in：{text[:14]}"
            row["issues"].append(msg)
            issues.append({"slide": idx + 1, "kind": "bounds", "detail": msg})
        if not bleeds and shape.top + shape.height > H + Emu(int(0.02 * EMU_IN)):
            msg = f"元素越出下边界：{text[:14]}"
            row["issues"].append(msg)
            issues.append({"slide": idx + 1, "kind": "bounds", "detail": msg})

        if len(text) > 190:
            msg = f"单块文字 {len(text)} 字，观众读不完：{text[:16]}"
            row["issues"].append(msg)
            issues.append({"slide": idx + 1, "kind": "density", "detail": msg})

        if sizes:
            biggest = max(sizes)
            smallest = min(sizes)
            if smallest < BODY_MIN and len(text) > 24 and not FURNITURE.match(text):
                msg = f"正文字号 {smallest:g}pt 偏小：{text[:16]}"
                row["issues"].append(msg)
                issues.append({"slide": idx + 1, "kind": "type", "detail": msg})
            decorative = re.fullmatch(r"[“”\"'「」『』—–\-·]+", text) or re.fullmatch(r"\d{2}", text)
            if biggest > TITLE_MAX and not decorative:
                msg = f"字号 {biggest:g}pt 超过 {TITLE_MAX}pt：{text[:16]}"
                row["issues"].append(msg)
                issues.append({"slide": idx + 1, "kind": "type", "detail": msg})

    inks: list[float] = []
    if png_dir:
        png_dir = Path(png_dir)
        for idx in range(len(prs.slides)):
            png = png_dir / f"{idx + 1:02d}.png"
            if not png.exists():
                continue
            got = _ink(png)
            if got is None:
                continue
            val, _bg = got
            per_slide[idx]["ink"] = round(val, 3)
            inks.append(val)
            if val < 0.025:
                msg = "这一页几乎没有内容"
                per_slide[idx]["issues"].append(msg)
                issues.append({"slide": idx + 1, "kind": "ink", "detail": msg})

    # Three consecutive slides with the same structure reads as a template rut.
    ruts = 0
    shapes = [r["shapes"] for r in per_slide]
    for i in range(len(shapes) - 2):
        if shapes[i] and shapes[i] == shapes[i + 1] == shapes[i + 2]:
            ruts += 1

    score = 100
    score -= 9 * sum(1 for i in issues if i["kind"] == "bounds")
    score -= 5 * sum(1 for i in issues if i["kind"] == "type")
    score -= 7 * sum(1 for i in issues if i["kind"] == "density")
    score -= 6 * sum(1 for i in issues if i["kind"] == "ink")
    score -= 8 * ruts
    score = max(0, score)
    median = round(sorted(inks)[len(inks) // 2], 3) if inks else None
    return {
        "file": pptx.name,
        "pages": len(prs.slides),
        "score": score,
        "ink_median": median,
        "ruts": ruts,
        "issues": issues,
        "detail": per_slide,
        "summary": f"{len(prs.slides)} 页 · 质量 {score} · {len(issues)} 个问题",
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python tools/qa.py deck.pptx [png_dir]")
        return 2
    png = argv[2] if len(argv) > 2 else None
    res = check(argv[1], png)
    print(res["summary"])
    for it in res["issues"][:20]:
        print(f"  p{it['slide']:>2} [{it['kind']}] {it['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

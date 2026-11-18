"""Measure the corpus instead of arguing about it.

Every number deck/theme.py uses -- the 0.75in margin, the 26pt title, the four
type sizes, the ink band -- was picked by reading about deck design. This turns
those opinions into measurements: what do real decks actually do?

Three decisions that decide whether the numbers mean anything:

  * **Normalise to a common canvas.** Canva exports 16:9 at 20x11.25in, i.e.
    1.5x the 13.333in that PowerPoint calls 16:9. A 23pt title on a 20in canvas
    is a 15.3pt title on ours. Comparing raw points across canvases compares
    zoom levels, not typography. Sizes are reported as pt on a 13.333in canvas
    and margins as a fraction of canvas width.
  * **Ink means content ink.** A first pass counted every non-full-bleed shape
    and got a 98% median -- which only says "these decks have a coloured
    background". Decoration is not content. Only shapes carrying text, pictures,
    tables or charts count.
  * **Only landscape decks.** The gallery also ships portrait A4 posters; those
    are a different medium and averaging them into margin statistics measures
    nothing useful.

Copy in a template is filler, so text density says little about a real report.
Positions, sizes and colours survive that difference; character counts do not.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from pptx import Presentation

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
EMU_IN = 914400
BASE_W = 13.333          # the canvas this project renders at
BACKGROUND = 0.85        # a shape covering this much of the page is a background


def _iter_shapes(shapes):
    for sh in shapes:
        yield sh
        if getattr(sh, "shapes", None) is not None:
            yield from _iter_shapes(sh.shapes)


def _color_of(fmt) -> str | None:
    try:
        if fmt.type == 1 and fmt.rgb is not None:      # MSO_FILL.SOLID
            return str(fmt.rgb)
    except Exception:  # noqa: BLE001
        return None
    return None


def _is_content(shape) -> bool:
    if getattr(shape, "has_text_frame", False) and (shape.text_frame.text or "").strip():
        return True
    if getattr(shape, "has_table", False) and shape.has_table:
        return True
    if getattr(shape, "has_chart", False) and shape.has_chart:
        return True
    try:
        return shape.shape_type == 13               # MSO_SHAPE_TYPE.PICTURE
    except Exception:  # noqa: BLE001
        return False


def measure(path: Path) -> dict | None:
    try:
        prs = Presentation(str(path))
    except Exception:  # noqa: BLE001
        return None
    sw, sh = prs.slide_width or 0, prs.slide_height or 0
    if not sw or not sh:
        return None
    W, H = sw / EMU_IN, sh / EMU_IN
    page_area = W * H
    k = BASE_W / W                                  # canvas -> 13.333in scale

    sizes: Counter[float] = Counter()
    colors: Counter[str] = Counter()
    slides = []

    for slide in prs.slides:
        boxes = []
        chars = 0
        page_sizes: set[float] = set()
        ink = 0.0
        for shape in _iter_shapes(slide.shapes):
            if None in (shape.left, shape.top, shape.width, shape.height):
                continue
            l, t = shape.left / EMU_IN, shape.top / EMU_IN
            w, h = shape.width / EMU_IN, shape.height / EMU_IN
            if w <= 0 or h <= 0:
                continue
            # Clamp to canvas: decorative shapes routinely hang off the edge,
            # and an unclamped box reports a negative margin.
            l2, t2 = max(0.0, l), max(0.0, t)
            r2, b2 = min(W, l + w), min(H, t + h)
            if r2 <= l2 or b2 <= t2:
                continue
            if (w * h) / page_area > BACKGROUND:
                continue
            if _is_content(shape):
                boxes.append((l2, t2, r2 - l2, b2 - t2))
                ink += (r2 - l2) * (b2 - t2) / page_area
            fill = _color_of(shape.fill) if getattr(shape, "fill", None) is not None else None
            if fill:
                colors[fill] += 1
            if getattr(shape, "has_text_frame", False):
                txt = shape.text_frame.text or ""
                chars += len("".join(txt.split()))
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if run.font.size:
                            pt = round(run.font.size.pt * k, 1)   # report at 13.333in
                            page_sizes.add(pt)
                            sizes[pt] += 1
                        try:
                            if run.font.color and run.font.color.rgb is not None:
                                colors[str(run.font.color.rgb)] += 1
                        except Exception:  # noqa: BLE001
                            pass
        if not boxes:
            slides.append({"empty": True})
            continue
        left = min(b[0] for b in boxes)
        right = max(b[0] + b[2] for b in boxes)
        top = min(b[1] for b in boxes)
        bottom = max(b[1] + b[3] for b in boxes)
        # Area-weighted centroid of the content, in canvas coordinates. This is
        # what R13 checks, so it has to be measured rather than assumed.
        area = sum(b[2] * b[3] for b in boxes) or 1.0
        cx = sum((b[0] + b[2] / 2) * b[2] * b[3] for b in boxes) / area / W
        cy = sum((b[1] + b[3] / 2) * b[2] * b[3] for b in boxes) / area / H
        slides.append({
            "cx": round(cx, 3),
            "cy": round(cy, 3),
            "content": len(boxes),
            "chars": chars,
            "sizes": sorted(page_sizes),
            "ml": round(left / W, 4),        # as a fraction of canvas width
            "mr": round((W - right) / W, 4),
            "mt": round(top / H, 4),         # as a fraction of canvas height
            "mb": round((H - bottom) / H, 4),
            "ink": round(min(ink, 1.0), 3),
        })
    return {
        "file": path.name,
        "w": round(W, 2), "h": round(H, 2),
        "ratio": round(W / H, 2),
        "scale": round(k, 3),
        "slides": slides,
        "sizes": dict(sizes),
        "colors": dict(colors.most_common(24)),
    }


def pct(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round(q * (len(vals) - 1)))))
    return vals[k]


def report(decks: list[dict]) -> str:
    pages = [s for d in decks for s in d["slides"] if not s.get("empty")]
    L: list[str] = []
    add = L.append
    add("# 语料测量报告\n")
    add(f"deck {len(decks)} 份，内容页 {len(pages)}。"
        f"字号统一折算到 13.333in 画布，边距折算成画布比例。\n")

    add("## 画布")
    add("")
    add("| 尺寸 | 比例 | 份数 | 折算系数 |")
    add("|---|---|---|---|")
    for (w, h), n in Counter((d["w"], d["h"]) for d in decks).most_common(6):
        add(f"| {w}×{h}in | {w/h:.2f} | {n} | ×{BASE_W/w:.3f} |")
    add("")

    add("## 页边距（占画布比例）")
    add("")
    add("| 边 | p10 | 中位 | p90 | 本仓库现值 | 折算英寸 |")
    add("|---|---|---|---|---|---|")
    for key, label, mine_in, axis in (
        ("ml", "左", 0.75, "w"), ("mr", "右", 0.75, "w"),
        ("mt", "上", 0.52, "h"), ("mb", "下", 0.46, "h"),
    ):
        v = [s[key] for s in pages]
        mine = mine_in / (BASE_W if axis == "w" else 7.5)
        add(f"| {label} | {pct(v,0.1):.1%} | {statistics.median(v):.1%} | "
            f"{pct(v,0.9):.1%} | {mine:.1%}（{mine_in}in） | — |")
    add("")

    add("## 单页字号种类数")
    add("")
    n = [len(s["sizes"]) for s in pages]
    hist = Counter(n)
    for kk in sorted(hist):
        add(f"- {kk} 种：{hist[kk]} 页（{hist[kk]/max(len(pages),1):.0%}）")
    add(f"- 中位 {statistics.median(n):.0f} 种，p90 {pct(n,0.9):.0f} 种")
    add("")

    all_sizes: Counter[float] = Counter()
    for d in decks:
        for kk, v in d["sizes"].items():
            all_sizes[float(kk)] += v
    add("## 全局字号频次（折算到 13.333in 画布，top 14）")
    add("")
    our = [38, 26, 18, 15, 14, 11, 9]
    for pt, cnt in all_sizes.most_common(14):
        near = min(our, key=lambda x: abs(x - pt))
        mark = " ←" if abs(near - pt) <= 1.5 else ""
        add(f"- {pt}pt：{cnt}{mark}")
    add("")
    add(f"（带 ← 表示本仓库阶梯里有 {1.5}pt 以内的对应档）")
    add("")

    add("## 墨迹覆盖率（内容面积 / 画布面积）")
    ink = [s["ink"] for s in pages]
    add(f"- p10 {pct(ink,0.1):.0%} / 中位 {statistics.median(ink):.0%} / p90 {pct(ink,0.9):.0%}")
    add("- 本仓库 audit 的合规带：35–70%")
    add("")

    add("## 视觉重心（内容面积加权的中心，占画布比例）")
    cx = [s["cx"] for s in pages]
    cy = [s["cy"] for s in pages]
    add(f"- 横：p10 {pct(cx,0.1):.0%} / 中位 {statistics.median(cx):.0%} / p90 {pct(cx,0.9):.0%}")
    add(f"- 纵：p10 {pct(cy,0.1):.0%} / 中位 {statistics.median(cy):.0%} / p90 {pct(cy,0.9):.0%}")
    add("- 本仓库 audit 的合规带：横 38–62%、纵 40–58%")
    add("")

    add("## 每页内容块数（文字/图/表/图表）")
    c = [s["content"] for s in pages]
    add(f"- p10 {pct(c,0.1):.0f} / 中位 {statistics.median(c):.0f} / p90 {pct(c,0.9):.0f}")
    add("")

    add("## 每页字数（模板占位文字，只当下限）")
    ch = [s["chars"] for s in pages]
    add(f"- p10 {pct(ch,0.1):.0f} / 中位 {statistics.median(ch):.0f} / p90 {pct(ch,0.9):.0f}")
    add("")

    add("## 最常用颜色（top 16）")
    add("")
    col: Counter[str] = Counter()
    for d in decks:
        for kk, v in d["colors"].items():
            col[kk] += v
    for cc, n2 in col.most_common(16):
        add(f"- #{cc}：{n2}")
    add("")
    return "\n".join(L)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    files = sorted(RAW.glob("*.pptx"))
    if not files:
        print("raw/ 里没有 pptx，先跑 crawl.py")
        return
    decks = []
    for f in files:
        d = measure(f)
        if not d:
            print(f"  {f.name} 打不开")
            continue
        if d["ratio"] < 1.3:
            print(f"  {f.name} 跳过（{d['w']}×{d['h']}，竖版）")
            continue
        decks.append(d)
        print(f"  {f.name} {d['w']}×{d['h']} ×{d['scale']} {len(d['slides'])} 页")
    if not decks:
        print("没有合格的横向 deck")
        return
    (ROOT / "stats.json").write_text(
        json.dumps(decks, ensure_ascii=False), encoding="utf-8"
    )
    text = report(decks)
    (ROOT / "REPORT.md").write_text(text, encoding="utf-8")
    print("\n" + text)


if __name__ == "__main__":
    main()

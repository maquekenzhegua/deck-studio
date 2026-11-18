"""Audit: measure a rendered deck against `rules.py`.

`verify.py` proves the deck is not lying. This proves it is not ugly — and
"ugly" is not a taste judgement here. It is seven numbers: type-scale
discipline, grid snapping, ink coverage, characters per page, density variance,
emphasis count, layout rhythm.

Everything is measured from the .pptx that will actually be projected, not from
the code that produced it. If `theme.py` says five font sizes and the file
contains 19 pt, this reports 19 pt.

    python -m deck.audit out/demo.pptx
    python -m deck.audit out/demo.pptx --plan out/demo.plan.json --json

Exit code is 1 when a `block` rule fails, so it can gate `build`.
"""

from __future__ import annotations

import argparse
import colorsys
import io
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu, Length, Pt

from . import rules as R
from .theme import FS_FOOT

FOOT_PT = FS_FOOT.pt
from . import theme
from .text import estimate_lines
from .theme import (
    COL_W, CONTENT_W, FS_BODY, FS_CAPTION, FS_FOOT, FS_SECTION, FS_TITLE,
    GUTTER, MARGIN_X, SLIDE_H, SLIDE_W,
)

EMU = 914400.0
CANVAS_W = float(SLIDE_W) / EMU
CANVAS_H = float(SLIDE_H) / EMU
# Every size the token file declares is legal, including cover/kpi/lead roles.
# Enumerating only five names here once let a 38pt cover and an 18pt lead read as
# violations — the scale lives in theme.py, so ask theme.py.
ALLOWED_SIZES = {round(getattr(theme, n).pt, 1)
                 for n in dir(theme) if n.startswith("FS_") and isinstance(getattr(theme, n), Length)}

# Column lines. Two conventions coexist and both are correct: an element that
# starts on column i sits at colx(i); an element that spans n columns is n*COL_W
# + (n-1)*GUTTER wide, so its right edge is one gutter short of colx(n). Accept
# both edges or every correctly placed multi-column block reads as misaligned.
COL_LINES: list[float] = []
for _i in range(13):
    _line = float(MARGIN_X + _i * (COL_W + GUTTER)) / EMU
    COL_LINES.extend([_line, _line - float(GUTTER) / EMU])
COL_LINES.append(float(MARGIN_X + CONTENT_W) / EMU)
COL_LINES.append(0.0)

GRID_X, GRID_Y = 120, 68
CELL_W, CELL_H = CANVAS_W / GRID_X, CANVAS_H / GRID_Y

CJK_STOP = re.compile(r"[\s，。、；：？！,.;:?!「」“”‘’（）()《》—\-·]")


@dataclass
class Box:
    left: int          # EMU
    top: int
    width: int
    height: int
    kind: str          # text | pic | shape
    text: str = ""
    sizes: list[float] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    align: str = "left"

    @property
    def l_in(self) -> float:
        return self.left / EMU

    @property
    def t_in(self) -> float:
        return self.top / EMU

    @property
    def w_in(self) -> float:
        return self.width / EMU

    @property
    def h_in(self) -> float:
        return self.height / EMU

    @property
    def r_in(self) -> float:
        return self.l_in + self.w_in

    @property
    def area(self) -> float:
        return self.w_in * self.h_in

    @property
    def is_bg(self) -> bool:
        """A decorative fill, not content that happens to be large.

        Area alone misclassifies: a full-width chart is 11.8 x 4.3in, past the
        50 sq in threshold, so a picture page read as "no content" and R04
        measured title-to-footer at 74%. Only a shape can be a background.
        """
        return self.kind == "shape" and self.area > 0.5 * CANVAS_W * CANVAS_H


@dataclass
class SlideMetrics:
    index: int
    role: str
    layout: str | None
    boxes: list[Box]
    chars: int
    sizes: dict[float, int]
    off_scale: list[float]
    ink: float
    snap: float
    centroid: tuple[float, float]
    accents: int
    title: str
    title_size: float
    title_lines: int
    title_box_h: float
    has_source: bool

    @property
    def n_sizes(self) -> int:
        """Distinct type sizes that carry content. The footer line is chrome:
        every page has it, so counting it would make the rule a page counter."""
        return len({x for x in self.sizes if x > FOOT_PT + 0.01})


@dataclass
class Report:
    path: str
    slides: list[SlideMetrics]
    problems: list[tuple[str, int, str]] = field(default_factory=list)
    deck: dict = field(default_factory=dict)

    @property
    def blocking(self) -> int:
        return sum(1 for rid, _, _ in self.problems if R.by_id(rid).gate == "block")

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "slides": [
                {
                    "index": s.index, "role": s.role, "layout": s.layout,
                    "chars": s.chars, "ink": round(s.ink, 4), "snap": round(s.snap, 3),
                    "sizes": sorted(s.sizes), "off_scale": s.off_scale,
                    "centroid": [round(s.centroid[0], 3), round(s.centroid[1], 3)],
                    "accents": s.accents, "title": s.title,
                    "title_size": s.title_size, "title_lines": s.title_lines,
                    "has_source": s.has_source,
                }
                for s in self.slides
            ],
            "deck": self.deck,
            "problems": [{"rule": r, "slide": i, "detail": d} for r, i, d in self.problems],
        }


# --- extraction -----------------------------------------------------------

# Accent means "the eye is pulled here". Hues the deck uses as *structure* --
# the hairline grey, the primary nav, the alert red -- are not accents, they are
# chrome. Counting them made every page with rules and a primary-coloured number
# read as over-emphasised, which is the opposite of what the rule checks.
CHROME = {
    "D8DDE5",   # HAIRLINE
    "D8DBDF",   # DATA_BAR
    "E3E6EB",   # CHART_GRID
    "E9EEF6",   # PRIMARY_SOFT
    "F6E9E6",   # ALERT_SOFT
    "FFFFFF",
}


def _is_colourful(hex_rgb: str) -> bool:
    try:
        r, g, b = (int(hex_rgb[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return False
    if hex_rgb.upper() in CHROME:
        return False
    _h, l, s = colorsys.rgb_to_hls(r, g, b)
    return s > 0.18 and 0.12 < l < 0.94


def _boxes(slide) -> list[Box]:
    out: list[Box] = []
    for sh in slide.shapes:
        if sh.left is None or sh.top is None or sh.width is None or sh.height is None:
            continue
        kind = "shape"
        if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
            kind = "pic"
        elif sh.has_text_frame and sh.text_frame.text.strip():
            kind = "text"
        b = Box(int(sh.left), int(sh.top), int(sh.width), int(sh.height), kind)
        if kind == "text":
            b.text = sh.text_frame.text
            try:
                al = sh.text_frame.paragraphs[0].alignment
                b.align = {1: "left", 2: "center", 3: "right", 4: "justify"}.get(
                    int(al) if al is not None else 1, "left")
            except (AttributeError, TypeError, ValueError):
                b.align = "left"
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    if r.font.size:
                        b.sizes.append(round(r.font.size.pt, 1))
                    try:
                        b.colors.append(str(r.font.color.rgb))
                    except (AttributeError, TypeError, ValueError):
                        pass
        elif kind == "shape":
            try:
                if sh.fill.type is not None and int(sh.fill.type) == 1:
                    b.colors.append(str(sh.fill.fore_color.rgb))
            except (AttributeError, TypeError, ValueError):
                pass
        out.append(b)
    return out


_PIC_CACHE: dict[tuple[int, int, int], float] = {}


def _picture_ink(sh) -> float:
    """Fraction of a placed picture that is not paper.

    A matplotlib chart with generous padding is 80% white; counting its
    bounding box as "ink" would flatter it. This counts pixels.
    """
    key = (int(sh.left), int(sh.top), int(sh.width))
    if key in _PIC_CACHE:
        return _PIC_CACHE[key]
    ratio = 0.55
    try:
        im = Image.open(io.BytesIO(sh.image.blob)).convert("RGB")
        im.thumbnail((180, 180))
        px = list(im.getdata())
        if px:
            n = sum(1 for r, g, b in px if min(r, g, b) < 238 or max(r, g, b) - min(r, g, b) > 14)
            ratio = n / len(px)
    except Exception:
        pass
    _PIC_CACHE[key] = ratio
    return ratio


def _ink(slide, boxes: list[Box]) -> float:
    weights = [[0.0] * GRID_X for _ in range(GRID_Y)]
    pics = {id(sh): sh for sh in slide.shapes if sh.shape_type == MSO_SHAPE_TYPE.PICTURE}
    by_geom = {(b.left, b.top, b.width, b.height): b for b in boxes}
    pic_ratio: dict[tuple, float] = {}
    for sh in pics.values():
        k = (int(sh.left), int(sh.top), int(sh.width), int(sh.height))
        pic_ratio[k] = _picture_ink(sh)

    for b in boxes:
        if b.is_bg:
            continue
        w = pic_ratio.get((b.left, b.top, b.width, b.height), 1.0) if b.kind == "pic" else 1.0
        x0 = max(0, int(b.l_in / CELL_W))
        x1 = min(GRID_X, int((b.r_in) / CELL_W) + 1)
        y0 = max(0, int(b.t_in / CELL_H))
        y1 = min(GRID_Y, int((b.t_in + b.h_in) / CELL_H) + 1)
        for y in range(y0, y1):
            row = weights[y]
            for x in range(x0, x1):
                if w > row[x]:
                    row[x] = w
    return sum(sum(r) for r in weights) / (GRID_X * GRID_Y)


def _hangs_off_a_marker(b: Box, boxes: list[Box]) -> bool:
    """True when this text starts just right of something that IS on a line.

    Numbered lists and marker-led notes are laid out as two boxes: the marker on
    the column line, the text one indent to its right. The pair is aligned; only
    the second box's left edge is not, so it is measured by its marker.
    """
    if b.kind != "text":
        return False
    if min(abs(b.l_in - c) for c in COL_LINES) <= R.SNAP_TOLERANCE_IN:
        return False
    for other in boxes:
        if other is b or other.is_bg:
            continue
        if min(abs(other.l_in - c) for c in COL_LINES) > R.SNAP_TOLERANCE_IN:
            continue
        if other.r_in > b.l_in or other.t_in >= b.t_in + b.h_in or \
                other.t_in + other.h_in <= b.t_in:
            continue
        if b.l_in - other.r_in <= 0.60:      # one indent, not a stray column
            return True
    return False


def _snap(boxes: list[Box]) -> float:
    """Share of alignment edges that sit on a column line.

    A left-aligned text box only hangs from its left edge; its right edge is
    wherever the wrap box happened to end and carries no alignment meaning.
    Rules and markers (one dimension < 0.25 in) are decorative: exempt.
    """
    edges: list[float] = []
    for b in boxes:
        # Pictures are contain-fitted (centred in their cell), so their edges
        # are a consequence of the aspect ratio, not an alignment decision.
        # Decorative rules and markers are too thin to carry an edge.
        if b.is_bg or b.kind == "pic" or (b.kind == "shape" and min(b.w_in, b.h_in) < 0.25):
            continue
        # A proportional bar's length is the datum. Snapping it to a column
        # would round the number it encodes, so it is exempt from alignment the
        # same way a picture is.
        if b.kind == "shape" and b.h_in <= 0.17 and b.w_in > b.h_in * 2:
            continue
        if b.align == "right":
            edges.append(b.r_in)
        elif b.align == "center":
            edges.extend([b.l_in, b.r_in])
        elif _hangs_off_a_marker(b, boxes):
            # A hanging indent: the marker sits on the column line and the text
            # starts one indent to its right. Measuring the text origin alone
            # reports 62% and reads as a layout defect when the layout is the
            # standard numbered-list pattern.
            continue
        else:
            edges.append(b.l_in)
    if not edges:
        return 1.0
    hits = sum(1 for e in edges if min(abs(e - c) for c in COL_LINES) <= R.SNAP_TOLERANCE_IN)
    return hits / len(edges)


def _centroid(boxes: list[Box]) -> tuple[float, float]:
    total = sum(b.area for b in boxes if not b.is_bg)
    if total <= 0:
        return (0.5, 0.5)
    cx = sum((b.l_in + b.w_in / 2) * b.area for b in boxes if not b.is_bg) / total
    cy = sum((b.t_in + b.h_in / 2) * b.area for b in boxes if not b.is_bg) / total
    return (cx / CANVAS_W, cy / CANVAS_H)


def _title(boxes: list[Box]) -> tuple[str, float, int, float]:
    """The title is the largest text *in the header band*, not on the page.

    Picking the largest text anywhere made a KPI page report its title as the
    60pt big number, because 60 > 26. Then R04 measured the band from the KPI
    down to the footnote and reported 12.3% for a page whose header was fine.
    """
    cands = [b for b in boxes if b.kind == "text" and b.sizes and not b.is_bg]
    if not cands:
        return ("", 0.0, 0, 0.0)
    # The title is what comes first and, among what comes first, the largest.
    # Neither test alone works: "largest anywhere" hands a KPI page its 60pt
    # big number, and "topmost anywhere" hands a numbered list its 01 marker.
    first = min(b.t_in for b in cands)
    lead = [b for b in cands if b.t_in <= first + 0.6]
    top = max(lead, key=lambda b: (max(b.sizes), -b.t_in))
    size = max(top.sizes)
    lines = estimate_lines(top.text, Emu(top.width), Pt(size))
    return (top.text.replace("\n", " "), size, lines, top.h_in)


def measure_slide(slide, index: int, role: str, layout: str | None) -> SlideMetrics:
    boxes = _boxes(slide)
    chars = sum(len(re.sub(r"\s", "", b.text)) for b in boxes if b.kind == "text")
    sizes: dict[float, int] = {}
    for b in boxes:
        for s in b.sizes:
            sizes[s] = sizes.get(s, 0) + 1
    off = sorted(s for s in sizes if min(abs(s - a) for a in ALLOWED_SIZES) > 0.5)
    colourful = {c.upper() for b in boxes for c in b.colors if _is_colourful(c)}
    title, tsize, tlines, tbox = _title(boxes)
    has_source = any("来源" in b.text or "source" in b.text.lower() for b in boxes)
    cx, cy = _centroid(boxes)
    return SlideMetrics(
        index=index, role=role, layout=layout, boxes=boxes, chars=chars, sizes=sizes,
        off_scale=off, ink=_ink(slide, boxes), snap=_snap(boxes), centroid=(cx, cy),
        accents=len(colourful), title=title, title_size=tsize, title_lines=tlines,
        title_box_h=tbox, has_source=has_source,
    )


# --- rules ----------------------------------------------------------------

NOUN_TAIL = ("分析", "情况", "汇报", "总结", "概述", "介绍", "说明", "复盘", "回顾")


def _bigrams(s: str) -> set[str]:
    t = CJK_STOP.sub("", s)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _jaccard(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def _title_band(s: SlideMetrics) -> float:
    """Height from the title's top to the first smaller text below it.

    Measuring the title textbox alone is meaningless (a 26 pt line in a
    one-line box is 4% of the canvas). What the eye reads as "the header" is
    the whole empty band above the body.
    """
    titled = [b for b in s.boxes
              if b.kind == "text" and not b.is_bg and s.title_size in b.sizes]
    if not titled:
        return 0.0
    tb = min(titled, key=lambda b: b.t_in)
    # "Below the title" means below it and visually secondary. A text box
    # qualifies when it is set smaller than the title. A picture or chart has no
    # type size at all, and the first version of this test skipped it -- so a
    # chart page measured title-to-footer (74% of the canvas) and R04 fired on a
    # page that was in fact fine.
    below = [b for b in s.boxes
             if not b.is_bg and b.top > tb.top + 0.05 * EMU
             and (b.kind != "text" or max(b.sizes or [0]) < s.title_size)
             and min(b.w_in, b.h_in) >= 0.25]
    if not below:
        return (tb.h_in) / CANVAS_H
    return (min(b.t_in for b in below) - tb.t_in) / CANVAS_H


def check(rep: Report) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    content = [s for s in rep.slides if s.role == "content"]

    for s in rep.slides:
        # R01 margins: the leftmost content edge is the deck's left margin.
        left_c = min((b.l_in for b in s.boxes if not b.is_bg), default=0.0)
        if s.role == "content" and not (CANVAS_W * R.MARGIN_X_RATIO[0] - 0.05
                                        <= left_c <= CANVAS_W * R.MARGIN_X_RATIO[1] + 0.05):
            out.append(("R01", s.index, f"左内容边距 {left_c:.2f}in，规范 0.80–0.93in"))

        # R02 grid snapping
        if s.role == "content" and s.snap < 0.90:
            out.append(("R02", s.index, f"仅 {s.snap:.0%} 的边缘落在列线上（<90%）"))

        # R03 type scale
        if s.off_scale:
            out.append(("R03", s.index,
                        f"越界字号 {', '.join(f'{x:g}' for x in s.off_scale)}pt，"
                        f"阶梯为 {sorted(ALLOWED_SIZES, reverse=True)}"))
        # Measured: median 3 sizes per page, p90 7. Four and five are ordinary
        # (headline, body, caption, source, footer). Past five it stops reading
        # as a hierarchy and starts reading as a collage.
        if s.n_sizes > 5:
            out.append(("R03", s.index, f"本页 {s.n_sizes} 种字号（>5）"))

        # R04 title band: title top to the first content element below it.
        if s.role == "content" and s.title_box_h:
            ratio = _title_band(s)
            if not (R.TITLE_HEIGHT_RATIO[0] <= ratio <= R.TITLE_HEIGHT_RATIO[1]):
                out.append(("R04", s.index,
                            f"标题带到正文占画布 {ratio:.1%}，"
                            f"规范 {R.TITLE_HEIGHT_RATIO[0]:.0%}–{R.TITLE_HEIGHT_RATIO[1]:.0%}"))

        # R05 line length: measure the wrap width of reading text, not of every
        # box. A one-line subtitle may be as wide as the page; a paragraph that
        # wraps past 34 em loses the reader at the line break. So: body size only,
        # and only when the string actually wraps at that width.
        for b in s.boxes:
            if b.kind != "text" or b.is_bg or not b.sizes:
                continue
            size = max(b.sizes)
            if abs(size - FS_BODY.pt) > 0.5:
                continue
            cap = b.w_in / (size / 72.0)
            chars = len(re.sub(r"\s", "", b.text))
            if cap > R.MAX_LINE_CHARS_CJK + 0.5 and chars > cap:
                out.append(("R05", s.index,
                            f"{size:g}pt 正文宽 {b.w_in:.2f}in ≈ {cap:.0f} 字/行，"
                            f"实际 {chars} 字会折行（>{R.MAX_LINE_CHARS_CJK}）"))

        # R06 chart font: a chart's label size lives in the source figure, not in
        # the .pptx — the picture only carries pixels. Guarded at the source by
        # `tests/test_audit.py::test_chart_source_fonts_are_projectable`.

        # R07 footer baseline: the bottom band's small elements share one line.
        feet = [b for b in s.boxes
                if b.kind == "text" and not b.is_bg and b.t_in > CANVAS_H - 0.80
                and max(b.sizes or [0]) <= FS_CAPTION.pt]
        if len(feet) >= 2:
            spread = max(b.t_in for b in feet) - min(b.t_in for b in feet)
            if spread > R.SNAP_TOLERANCE_IN:
                out.append(("R07", s.index, f"页脚元素基线相差 {spread:.2f}in（>0.02）"))

        # R09 / R08 per-slide density
        if s.role == "content":
            lo, hi = (R.CHARS_CHART if s.layout in ("chart", "key_figure") else R.CHARS_CONTENT)
            # A picture page is mostly picture; 80 characters of caption is not
            # a defect, it is the point.
            if s.layout in ("image_full", "split_image"):
                pass
            elif not (lo <= s.chars <= hi):
                out.append(("R09", s.index, f"{s.chars} 字，规范 {lo}–{hi}"))
            if not (R.INK_CONTENT[0] <= s.ink <= R.INK_CONTENT[1]):
                out.append(("R08", s.index,
                            f"墨迹 {s.ink:.0%}，规范 {R.INK_CONTENT[0]:.0%}–{R.INK_CONTENT[1]:.0%}"))
            if s.accents > R.EMPHASIS_PER_PAGE:
                out.append(("R11", s.index, f"{s.accents} 处彩色强调（>2）"))
            cx, cy = s.centroid
            if not (R.CENTROID_X[0] <= cx <= R.CENTROID_X[1]
                    and R.CENTROID_Y[0] <= cy <= R.CENTROID_Y[1]):
                out.append(("R13", s.index, f"视觉重心 ({cx:.0%},{cy:.0%}) 偏离中心区"))
            if not s.has_source and s.layout in ("chart", "key_figure"):
                out.append(("R17", s.index, "数据页没有来源行"))

        # R14 action title
        if s.role == "content" and s.title:
            if s.title.rstrip().endswith(NOUN_TAIL) or not re.search(r"[，,。:：]|\d", s.title):
                out.append(("R14", s.index, f"标题像目录条目：{s.title[:26]}"))

    # R10 density variance
    if len(content) >= 3:
        vals = [s.chars for s in content]
        cv = statistics.pstdev(vals) / (statistics.mean(vals) or 1)
        rep.deck["density_cv"] = round(cv, 3)
        if cv > R.DENSITY_CV_MAX:
            out.append(("R10", 0, f"内容页字数变异系数 {cv:.2f}（>{R.DENSITY_CV_MAX}），忽空忽满"))

    # R15 title chain
    for a, b in zip(rep.slides, rep.slides[1:]):
        j = _jaccard(a.title, b.title)
        if j > R.TITLE_SIMILARITY_MAX:
            out.append(("R15", b.index, f"与上一页标题重复度 {j:.0%}：{b.title[:24]}"))

    # R12 rhythm
    layouts = [s.layout for s in rep.slides if s.layout]
    if layouts:
        runs, cur = [], 1
        for x, y in zip(layouts, layouts[1:]):
            if x == y:
                cur += 1
            else:
                runs.append(cur)
                cur = 1
        runs.append(cur)
        rep.deck["layout_runs"] = runs
        rep.deck["layout_kinds"] = len(set(layouts))
        if max(runs) > R.LAYOUT_RUN_MAX:
            out.append(("R12", 0, f"同一版式连续 {max(runs)} 页（>2）"))
        share = max(layouts.count(x) for x in set(layouts)) / len(layouts)
        rep.deck["top_layout_share"] = round(share, 3)
        if share > R.LAYOUT_SHARE_MAX:
            out.append(("R12", 0, f"单一版式占 {share:.0%}（>40%）"))
        if len(set(layouts)) < R.MIN_LAYOUTS:
            out.append(("R12", 0, f"全 deck 仅 {len(set(layouts))} 种版式（<4）"))

    return out


# --- entry points ---------------------------------------------------------

def audit(path: str, plan_path: str | None = None) -> Report:
    prs = Presentation(path)
    layouts: list[str] | None = None
    if plan_path:
        data = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        layouts = [s.get("layout") for s in data.get("slides", [])]
    if layouts is None:
        layouts = [None] * len(prs.slides)

    slides: list[SlideMetrics] = []
    n = len(prs.slides)
    for i, (slide, layout) in enumerate(zip(prs.slides, layouts), 1):
        role = "cover" if i == 1 else ("closing" if i == n and n > 2 else "content")
        if layout in ("title", "closing"):
            role = "cover" if layout == "title" else "closing"
        slides.append(measure_slide(slide, i, role, layout))

    rep = Report(path=str(path), slides=slides)
    rep.problems = check(rep)
    rep.deck["slides"] = n
    rep.deck["blocking"] = rep.blocking
    return rep


def render_text(rep: Report) -> str:
    lines = [f"审计 {rep.path} · {len(rep.slides)} 页"]
    head = f"{'页':>2} {'角色':<7} {'字数':>4} {'墨迹':>5} {'吸格':>5} {'字号':<18} {'彩色':>3} 标题"
    lines.append(head)
    lines.append("-" * 96)
    for s in rep.slides:
        sz = ",".join(f"{x:g}" for x in sorted(s.sizes, reverse=True))
        lines.append(
            f"{s.index:>2} {s.role:<7} {s.chars:>4} {s.ink:>4.0%} {s.snap:>4.0%} "
            f"{sz:<18} {s.accents:>3} {s.title[:34]}"
        )
    if rep.deck:
        d = rep.deck
        bits = [f"{k}={v}" for k, v in d.items() if k not in ("slides", "blocking")]
        lines.append("-" * 96)
        lines.append("deck: " + "  ".join(bits))

    lines.append("")
    if not rep.problems:
        lines.append("√ 17 条规则全部通过")
    else:
        lines.append(f"× {len(rep.problems)} 处偏离（阻断 {rep.blocking}）")
        for rid, page, detail in rep.problems:
            r = R.by_id(rid)
            where = f"p{page}" if page else "deck"
            lines.append(f"  [{rid} {r.name}] {where} {detail}")
    return "\n".join(lines)


# --- R06: the one rule a .pptx cannot answer ------------------------------
# A chart's label size lives in the source figure; the slide only holds pixels,
# so the file gives no way to recover it. Instead of pretending to measure, read
# the code that draws the figures and report the literals against the floor.
CHART_FONT_RE = re.compile(r"(labelsize|fontsize)\s*=\s*([0-9]+(?:\.[0-9]+)?)")


def chart_fonts(path: str = "deck/charts.py") -> list[tuple[float, int]]:
    """Every (pt, line) literal below MIN_CHART_FONT_PT, in source order."""
    src = Path(path)
    if not src.exists():
        return []
    out: list[tuple[float, int]] = []
    for i, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
        for _k, pt in CHART_FONT_RE.findall(line):
            if float(pt) < R.MIN_CHART_FONT_PT:
                out.append((float(pt), i))
    return out


# --- R02's upstream cause: fractional columns ----------------------------
# `col(4.5)` and `colx(7.85)` are the only way a layout can be "on-grid" in its
# author's head and off-grid in the file: the grid has 12 integer lines, so any
# fractional index lands between them by construction. Snapping is measured on
# the .pptx; this points at the line that made it fail.
FRACTIONAL_COL_RE = re.compile(r"\b(colx?)\(\s*(\d+\.\d+)\s*\)")


def fractional_columns(path: str = "deck/render.py") -> list[tuple[str, float, int]]:
    """(call, index, line) for every non-integer column reference in source."""
    src = Path(path)
    if not src.exists():
        return []
    out: list[tuple[str, float, int]] = []
    for i, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
        for call, idx in FRACTIONAL_COL_RE.findall(line):
            out.append((call, float(idx), i))
    return out


def cli_report(pptx: str, plan: str | None = None, as_json: bool = False) -> int:
    """Print a report and return the gate code. Shared by `deck.audit` and
    `deck.cli audit` so the two can never disagree."""
    rep = audit(pptx, plan)
    if as_json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
        return 1 if rep.blocking else 0

    print(render_text(rep))
    # Two rules cannot be answered by the .pptx: R06 lives in the source figure,
    # R02's cause lives in the source layout. Read the source and say so.
    frac = fractional_columns()
    if frac:
        seen = ", ".join(f"L{l} {c}({i:g})" for c, i, l in frac[:6])
        print(f"! [R02 上游] render.py 有 {len(frac)} 处小数栏位：{seen}"
              f" —— 12 列网格只有整数线，小数栏位必落在两条线之间")
    below = chart_fonts()
    if below:
        worst = ", ".join(f"L{l} {pt:g}pt" for pt, l in below[:6])
        print(f"! [R06 图表内字号] charts.py 有 {len(below)} 处 < {R.MIN_CHART_FONT_PT:g}pt："
              f"{worst}（源图贴满版后约 {below[0][0] * 0.9:.1f}pt，投影不可读）")
    return 1 if rep.blocking else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deck.audit", description="量一份 pptx 是否符合设计规则")
    ap.add_argument("pptx")
    ap.add_argument("--plan", help="plan.json，用来判断版式节奏（可选）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    return cli_report(args.pptx, args.plan, args.json)


if __name__ == "__main__":
    raise SystemExit(main())

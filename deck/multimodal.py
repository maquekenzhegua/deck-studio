"""Multimodal enrichment: look at the pictures instead of only listing them.

`ingest` already pulls images out of folders, decks and documents. That is not
the same as understanding them: a chart, a site photo and a screenshot need
different pages, different crops and different captions. This module does the
cheap, deterministic part locally (kind, palette, brightness, aspect) and
leaves an optional vision model hook for prose.

No network is required. Everything here is PIL + zipfile + PyMuPDF.
"""

from __future__ import annotations

import colorsys
import io
import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageFilter

from . import vision
from .ingest import Bundle, Picture

KIND_ZH = {"photo": "照片", "chart": "图表", "screenshot": "截图",
           "logo": "标识", "page": "文档页", "flat": "纯色图"}

_HUES = [(0, "红"), (16, "橙"), (45, "黄"), (70, "黄绿"), (100, "绿"),
         (160, "青"), (195, "蓝"), (250, "靛"), (280, "紫"), (320, "品红"),
         (345, "红")]


def _color_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = [v / 255 for v in rgb]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.12:
        if v > 0.86:
            return "白"
        if v < 0.16:
            return "黑"
        return "灰"
    deg = h * 360
    name = "红"
    for start, label in _HUES:
        if deg >= start:
            name = label
    return name


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def analyse(path: str | Path) -> dict:
    """Kind, palette, brightness and shape of one image, without a model."""
    p = Path(path)
    out = {"kind": "photo", "palette": [], "brightness": 0.5,
           "aspect": 1.0, "white": 0.0, "colors": 0, "width": 0, "height": 0}
    try:
        with Image.open(p) as raw:
            im = raw.convert("RGB")
            out["width"], out["height"] = im.size
            out["aspect"] = round(im.size[0] / max(im.size[1], 1), 3)
            im.thumbnail((360, 360))
            px = list(im.getdata())
            n = max(len(px), 1)
            white = sum(1 for r, g, b in px if r > 236 and g > 236 and b > 236) / n
            lum = sum(0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in px) / n / 255
            edges = im.convert("L").filter(ImageFilter.FIND_EDGES)
            edge = sum(edges.getdata()) / n
            q = im.quantize(colors=6, method=Image.MEDIANCUT)
            pal = q.getpalette() or []
            counts = sorted(q.getcolors() or [], reverse=True)
            palette = []
            for count, idx in counts[:4]:
                rgb = tuple(pal[idx * 3: idx * 3 + 3])
                if len(rgb) == 3:
                    palette.append({"hex": _hex(rgb), "name": _color_name(rgb),
                                    "share": round(count / n, 3)})
            out.update(palette=palette, brightness=round(lum, 3),
                       white=round(white, 3), colors=len(set(px)),
                       edge=round(edge, 2))
            if white > 0.55 and edge >= 5:
                out["kind"] = "chart"
            elif white > 0.72 and edge < 5:
                out["kind"] = "logo" if out["colors"] < 80 else "screenshot"
            elif out["colors"] < 24 and edge < 3:
                out["kind"] = "flat"
            elif white > 0.45 and out["aspect"] > 1.5:
                out["kind"] = "screenshot"
            else:
                out["kind"] = "photo"
    except Exception:  # noqa: BLE001
        pass
    return out


def pptx_charts(path: Path) -> list[dict]:
    """Read the numbers inside a deck's charts.

    A previous year's deck is often the only place the numbers still exist.
    """
    return _charts_in(path, "ppt/charts/chart")


def _sheet_values(wb, ref: str) -> list:
    """Resolve `'数据'!$B$2:$B$4` against the workbook, in file order."""
    from openpyxl.utils.cell import range_boundaries

    if not ref:
        return []
    sheet, _, cells = ref.rpartition("!")
    sheet = sheet.strip().strip("'")
    cells = cells.replace("$", "")
    if ":" not in cells:
        cells = f"{cells}:{cells}"
    try:
        min_col, min_row, max_col, max_row = range_boundaries(cells)
        ws = wb[sheet] if sheet else wb.active
    except Exception:  # noqa: BLE001 - a broken reference is not a crash
        return []
    out: list = []
    for row in ws.iter_rows(min_row=min_row, max_row=max_row,
                            min_col=min_col, max_col=max_col, values_only=True):
        out.append(row[0] if row else None)
    return out


def xlsx_charts(path: Path) -> list[dict]:
    """Read the numbers inside a workbook's native charts.

    A chart is often the only place a series was ever written down. Excel
    keeps a cached copy of the values in the chart part; openpyxl and some
    exporters do not, so when the cache is empty the cell reference beside it
    is resolved against the workbook instead.
    """
    charts = _charts_in(path, "xl/charts/chart")
    if not any(not c["vals"] for c in charts):
        return charts
    try:
        from openpyxl import load_workbook

        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception:  # noqa: BLE001
        return charts
    try:
        for chart in charts:
            if chart["vals"]:
                continue
            vals = [float(v) for v in _sheet_values(wb, chart.get("val_ref", ""))
                    if isinstance(v, (int, float))]
            cats = [str(c).strip() for c in _sheet_values(wb, chart.get("cat_ref", ""))
                    if c is not None and str(c).strip()]
            n = min(len(vals), len(cats)) if cats else len(vals)
            if n < 2:
                continue
            chart["vals"] = vals[:n]
            chart["cats"] = cats[:n] if cats else [str(i + 1) for i in range(n)]
            if not chart.get("series") and chart.get("tx_ref"):
                head = _sheet_values(wb, chart["tx_ref"])
                if head and str(head[0] or "").strip():
                    chart["series"] = str(head[0]).strip()
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass
    return charts
    return out


def caption(p: Picture) -> str:
    """A short, honest label. The vision pass may replace it with a sentence."""
    if p.kind == "page" and p.caption:
        return p.caption
    kind = KIND_ZH.get(p.kind or "photo", "图片")
    stem = Path(p.name).stem
    stem = re.sub(r"[-_]s\d+-\d+$", "", stem)
    return f"{stem} · {kind}"


def enrich_picture(p: Picture) -> Picture:
    info = analyse(p.path)
    p.kind = info["kind"]
    p.palette = info["palette"]
    p.note = (f"{info['width']}×{info['height']} · 亮度 {info['brightness']:.2f}"
              f" · 白底 {info['white']:.0%}")
    # A vision model, when the operator has configured one, replaces the
    # geometry note with what the picture actually shows. Offline unchanged.
    desc = vision.describe(p.path)
    if desc:
        p.note = desc
    p.caption = caption(p)
    return p


# --------------------------------------------------------------------------
# containers: docx media, pdf pages, pptx theme
# --------------------------------------------------------------------------


def _next_id(pics: list[Picture]) -> str:
    n = 1
    used = {p.id for p in pics}
    while f"p{n}" in used:
        n += 1
    return f"p{n}"


def docx_media(path: Path, pics: list[Picture], out_dir: Path, limit: int = 12) -> int:
    added = 0
    have = {Path(p.path).name for p in pics}
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.startswith("word/media/") or len(pics) >= 24 or added >= limit:
                    continue
                data = z.read(name)
                dest = out_dir / f"{path.stem}-{Path(name).name}"
                if dest.name in have:
                    continue
                dest.write_bytes(data)
                try:
                    with Image.open(dest) as im:
                        w, h = im.size
                except Exception:  # noqa: BLE001
                    dest.unlink(missing_ok=True)
                    continue
                pics.append(Picture(id=_next_id(pics), name=dest.name, path=str(dest),
                                    width=w, height=h))
                added += 1
    except Exception:  # noqa: BLE001
        pass
    return added


def pdf_pages(path: Path, pics: list[Picture], out_dir: Path, *, dpi: int = 110,
              limit: int = 3) -> int:
    """Render pages only when the PDF gave us little text (a scanned deck)."""
    added = 0
    try:
        import fitz

        with fitz.open(path) as doc:
            for i, page in enumerate(doc, 1):
                if i > limit or len(pics) >= 24:
                    break
                pix = page.get_pixmap(dpi=dpi)
                dest = out_dir / f"{path.stem}-p{i}.png"
                pix.save(str(dest))
                pics.append(Picture(id=_next_id(pics), name=dest.name, path=str(dest),
                                    width=pix.width, height=pix.height,
                                    kind="page", caption=f"{path.stem} 第 {i} 页"))
                added += 1
    except Exception:  # noqa: BLE001
        pass
    return added


_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def pptx_theme(path: Path) -> dict:
    """Read the colour + font scheme the deck already uses."""
    out: dict = {}
    try:
        with zipfile.ZipFile(path) as z:
            raw = z.read("ppt/theme/theme1.xml")
        root = ET.fromstring(raw)
        scheme = root.find("a:themeElements/a:clrScheme", _NS)
        colors: dict[str, str] = {}
        if scheme is not None:
            for node in list(scheme):
                tag = node.tag.split("}")[-1]
                srgb = node.find("a:srgbClr", _NS)
                sysclr = node.find("a:sysClr", _NS)
                if srgb is not None and srgb.get("val"):
                    colors[tag] = "#" + srgb.get("val").upper()
                elif sysclr is not None and sysclr.get("lastClr"):
                    colors[tag] = "#" + sysclr.get("lastClr").upper()
        fonts = {}
        for key in ("majorFont", "minorFont"):
            node = root.find(f"a:themeElements/a:fontScheme/a:{key}", _NS)
            if node is None:
                continue
            latin = node.find("a:latin", _NS)
            ea = node.find("a:ea", _NS)
            fonts[key] = {"latin": latin.get("typeface") if latin is not None else "",
                          "ea": ea.get("typeface") if ea is not None else ""}
        # Typography carries as much of the template as colour does: read the
        # master's title size so an adopted deck keeps its own scale.
        title_pt = 0.0
        try:
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.startswith("ppt/slideMasters/slideMaster")]
                for n in sorted(names)[:1]:
                    mroot = ET.fromstring(z.read(n))
                    for pr in mroot.iter():
                        if pr.tag.endswith("}titleStyle"):
                            lvl = pr.find("a:lvl1pPr", _NS)
                            rpr = lvl.find("a:defRPr", _NS) if lvl is not None else None
                            if rpr is not None and rpr.get("sz"):
                                title_pt = int(rpr.get("sz")) / 100.0
                            break
        except Exception:  # noqa: BLE001
            title_pt = 0.0
        out = {"colors": colors, "fonts": fonts, "title_pt": title_pt}
    except Exception:  # noqa: BLE001
        pass
    return out


# --------------------------------------------------------------------------
# one entry point
# --------------------------------------------------------------------------


def _charts_in(path: Path, prefix: str) -> list[dict]:
    """Read chart XML from any OOXML package (deck or workbook).

    PowerPoint and Excel store charts with the same DrawingML chart part; only
    the folder differs. Pulling the parser out means a workbook chart is not a
    second implementation that can rot independently.
    """
    out: list[dict] = []
    ns = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
          "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist() if n.startswith(prefix))
            for name in names:
                root = ET.fromstring(z.read(name))
                title = ""
                tnode = root.find(".//c:title", ns)
                if tnode is not None:
                    title = "".join(t.text or "" for t in tnode.iter()
                                    if t.tag.endswith("}t")).strip()
                cats: list[str] = []
                for pt in root.findall(".//c:cat//c:pt", ns)[:24]:
                    cats.append((pt.findtext("c:v", default="", namespaces=ns) or "").strip())
                vals: list[float] = []
                for pt in root.findall(".//c:val//c:pt", ns)[:24]:
                    raw = pt.findtext("c:v", default="", namespaces=ns) or ""
                    try:
                        vals.append(float(raw))
                    except ValueError:
                        vals.append(0.0)
                series = ""
                tx_ref = ""
                ser = root.find(".//c:ser", ns)
                if ser is not None:
                    sname = ser.find(".//c:tx//c:v", ns)
                    series = (sname.text or "").strip() if sname is not None else ""
                    if not series:
                        tref = ser.find(".//c:tx//c:f", ns)
                        if tref is not None and tref.text:
                            tx_ref = tref.text.strip()
                cat_ref = ""
                cref = root.find(".//c:cat//c:f", ns)
                if cref is not None and cref.text:
                    cat_ref = cref.text.strip()
                val_ref = ""
                vref = root.find(".//c:val//c:f", ns)
                if vref is not None and vref.text:
                    val_ref = vref.text.strip()
                numfmt = root.find(".//c:numFmt", ns)
                code = (numfmt.get("formatCode") if numfmt is not None else "") or ""
                unit = "ratio" if "%" in code else ("money" if any(
                    k in code for k in ("¥", "$", "€", "0,0")) else "")
                n = min(len(cats), len(vals))
                # A chart part with no cache is still worth keeping when it
                # names its cells: the workbook can answer for it below.
                if n >= 2 or (val_ref and not vals):
                    out.append({"title": title or f"{path.stem} 图表 {len(out) + 1}",
                                "series": series, "cats": cats[:n],
                                "vals": vals[:n], "unit": unit,
                                "cat_ref": cat_ref, "val_ref": val_ref,
                                "tx_ref": tx_ref})
    except Exception:  # noqa: BLE001
        pass
    return out


def _chart_table(bundle, chart: dict, src: Path) -> None:
    """Turn one embedded chart into a table the fact extractor understands."""
    import pandas as pd
    from .ingest import Table  # local import: ingest imports this module

    if any(t.name == chart["title"] for t in bundle.tables):
        return
    col = (chart.get("series") or "").strip() or \
        {"money": "金额", "ratio": "占比"}.get(chart.get("unit", ""), "数量")
    df = pd.DataFrame({"项目": chart["cats"], col: chart["vals"]})
    bundle.tables.append(Table(name=chart["title"], df=df,
                               source=f"{src.name} / 图表", sheet="图表"))


def augment(b: Bundle, root: str | Path, *, pages: bool = True) -> Bundle:
    """Classify every picture; pull missing media and theme from containers."""
    root = Path(root)
    out_dir = Path(tempfile.mkdtemp(prefix="deck-media-"))
    for p in b.pictures:
        enrich_picture(p)

    for doc in b.docs:
        src = Path(doc.source)
        if src.suffix.lower() == ".docx" and len(b.pictures) < 24:
            docx_media(src, b.pictures, out_dir)

    if pages:
        for doc in b.docs:
            src = Path(doc.source)
            if src.suffix.lower() == ".pdf" and len(doc.text) < 600:
                pdf_pages(src, b.pictures, out_dir)

    # A workbook's own charts carry cached values the cells may no longer hold.
    books = {Path(t.source) for t in b.tables
             if Path(t.source).suffix.lower() in (".xlsx", ".xlsm")}
    for src in sorted(books):
        for chart in xlsx_charts(src):
            _chart_table(b, chart, src)

    for outline in b.outlines:
        src = Path(outline.source)
        if src.suffix.lower() == ".pptx":
            theme = pptx_theme(src)
            if theme:
                b.meta.setdefault("pptx_themes", []).append(
                    {"file": src.name, **theme})
            for chart in pptx_charts(src):
                _chart_table(b, chart, src)
    for p in b.pictures:
        if not p.kind:
            enrich_picture(p)
    return b

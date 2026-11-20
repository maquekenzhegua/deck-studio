"""Visual layer v2: theme, typography, surfaces, pictures.

Why this module exists next to `theme.py`: v1 treated "design" as a fixed set
of constants, which is why every deck came out looking like the same annual
report. Consulting decks are not one look; they are a *system* — a palette, a
type pairing, a way of cropping photography, and a rhythm between light and
dark pages. This module makes that system an object you can swap.

Hard rules enforced here rather than trusted to layouts:

* colour comes from the theme, never from a literal at the call site;
* every text box gets an explicit CJK typeface (`a:ea`), because python-pptx
  otherwise lets PowerPoint pick a serif fallback for Chinese;
* pictures are cropped, never stretched, and can be duotoned so a folder of
  random screenshots still reads as one deck;
* gradients and transparencies are real XML, not a flat rectangle pretending.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

# --------------------------------------------------------------------------
# canvas
# --------------------------------------------------------------------------

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
MARGIN_X = Inches(0.62)
MARGIN_TOP = Inches(0.40)
MARGIN_BOTTOM = Inches(0.30)
GUTTER = Inches(0.18)
CONTENT_W = SLIDE_W - 2 * MARGIN_X
CONTENT_H = SLIDE_H - MARGIN_TOP - MARGIN_BOTTOM
COL_W = int((CONTENT_W - 11 * GUTTER) / 12)

BODY_TOP = Inches(1.52)
BODY_H = SLIDE_H - BODY_TOP - Inches(0.52)
FOOT_TOP = SLIDE_H - Inches(0.36)


def col(n: int) -> Emu:
    return Emu(int(n * COL_W + (n - 1) * GUTTER))


def colx(n: int) -> Emu:
    return Emu(int(MARGIN_X + n * (COL_W + GUTTER)))


# --------------------------------------------------------------------------
# theme
# --------------------------------------------------------------------------


_FONT_CACHE: dict[str, bool] = {}


def _installed(family: str) -> bool:
    """Is this typeface actually on the machine? A theme that names a font we
    do not have renders in a silent fallback — worse than our default."""
    key = (family or "").strip().lower()
    if not key:
        return False
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    ok = False
    try:
        import matplotlib.font_manager as fm  # noqa: PLC0415

        names = {f.name.lower() for f in fm.fontManager.ttflist}
        ok = key in names
    except Exception:  # noqa: BLE001
        ok = False
    _FONT_CACHE[key] = ok
    return ok


def _rgb(value: str) -> RGBColor:
    value = value.lstrip("#")
    return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    paper: str          # page background
    ink: str            # primary text
    ink2: str           # secondary text
    muted: str          # captions / axis
    hair: str           # rules
    soft: str           # tinted surface
    accent: str         # the one colour that carries meaning
    accent2: str        # second data colour, used sparingly
    dark: str           # dark field for cover / dividers
    dark_ink: str
    dark_muted: str
    neg: str = ""       # reserved for bad news; never a decorative second colour
    headline_latin: str = "Segoe UI"
    headline_cjk: str = "Microsoft YaHei"
    body_latin: str = "Segoe UI"
    body_cjk: str = "Microsoft YaHei"
    serif_headline: bool = False
    head_pt: float = 0.0     # adopted from a template's master; 0 = our default

    # --- derived ---------------------------------------------------------
    @property
    def is_dark(self) -> bool:
        return self.paper.lower() in {"#0b0f14", "#0e1116", "#0a0b0f"}

    @property
    def accent_rgb(self) -> RGBColor:
        return _rgb(self.accent)

    def rgb(self, field: str) -> RGBColor:
        return _rgb(getattr(self, field))

    @property
    def negative(self) -> str:
        return self.neg or self.accent2


THEMES: dict[str, Theme] = {
    # Deep navy, one vivid blue, warm red reserved for bad news. The closest
    # thing to a consulting default, but with a colder, more modern blue.
    "navy": Theme(
        name="navy", label="深海蓝 · 咨询",
        paper="#FFFFFF", ink="#0E1726", ink2="#3D4A5C", muted="#7C8797",
        hair="#DCE3EC", soft="#F1F4F9", accent="#1D4ED8", accent2="#C2410C",
        dark="#0A1A33", dark_ink="#F7FAFF", dark_muted="#9FB2CC",
        neg="#C2410C",
    ),
    # Warm paper, ink, burnt orange, serif headlines. Reads like a printed
    # strategy review rather than a template.
    "editorial": Theme(
        name="editorial", label="纸感 · 编辑",
        paper="#FBF9F4", ink="#17140F", ink2="#4A4238", muted="#8A8174",
        hair="#DED6C8", soft="#F1EBDF", accent="#B4441F", accent2="#1F4E5F",
        dark="#171310", dark_ink="#FBF7EE", dark_muted="#B8AC99",
        neg="#1F4E5F", headline_latin="Georgia", headline_cjk="Source Han Serif SC",
        body_latin="Segoe UI", body_cjk="Noto Sans SC", serif_headline=True,
    ),
    # Near-black ink with a single electric blue. For tech / product decks.
    "tech": Theme(
        name="tech", label="极简 · 科技",
        paper="#F7F8FB", ink="#0B0D12", ink2="#3B4250", muted="#7E8697",
        hair="#DFE3EA", soft="#EDF0F6", accent="#3D5AFE", accent2="#00A3A3",
        dark="#0A0C11", dark_ink="#F5F7FF", dark_muted="#98A2B8",
        neg="#E5484D", headline_latin="Segoe UI", headline_cjk="Noto Sans SC",
    ),
    # Muted green, cream, terracotta. Sustainability / healthcare / culture.
    "forest": Theme(
        name="forest", label="松墨 · 稳健",
        paper="#FBFCF9", ink="#10201A", ink2="#3C4F45", muted="#7C8C81",
        hair="#D9E2DA", soft="#EBF1EA", accent="#0F766E", accent2="#B45309",
        dark="#0B2B24", dark_ink="#F4FAF6", dark_muted="#9DBBAD",
        neg="#B45309",
    ),
    # Full dark deck: for launches and keynotes, not for printing.
    "midnight": Theme(
        name="midnight", label="午夜 · 发布",
        paper="#0B0F14", ink="#F2F5FA", ink2="#B9C4D4", muted="#7C8798",
        hair="#243040", soft="#141B25", accent="#5EEAD4", accent2="#F5C87A",
        dark="#070A0E", dark_ink="#F7FAFF", dark_muted="#93A1B5",
        neg="#F87171", headline_latin="Segoe UI", headline_cjk="Noto Sans SC",
    ),
}

DEFAULT_THEME = "navy"


def get_theme(name: str | None) -> Theme:
    if not name:
        return THEMES[DEFAULT_THEME]
    key = name.strip().lower()
    aliases = {
        "consulting": "navy", "mckinsey": "navy", "blue": "navy",
        "warm": "editorial", "paper": "editorial", "serif": "editorial",
        "dark": "midnight", "keynote": "midnight",
        "green": "forest", "minimal": "tech", "product": "tech",
    }
    return THEMES.get(key) or THEMES.get(aliases.get(key, ""), THEMES[DEFAULT_THEME])


# --------------------------------------------------------------------------
# low-level drawing
# --------------------------------------------------------------------------


def _no_line(shape) -> None:
    shape.line.fill.background()


def _no_shadow(shape) -> None:
    """Flat shapes, everywhere.

    `shadow.inherit = False` writes an empty <a:effectLst/>, but the shape still
    carries a <p:style> whose effectRef asks for a shadow — PowerPoint honours
    the empty list, LibreOffice does not, and the result is a soft drop shadow
    under every bar. Drop the style block as well.
    """
    try:
        shape.shadow.inherit = False
    except Exception:  # noqa: BLE001 - not every shape exposes shadow
        pass
    sp = shape._element
    style = sp.find(qn("p:style"))
    if style is not None:
        sp.remove(style)


def add_rect(slide, left, top, width, height, fill: RGBColor, alpha: int | None = None,
             line: RGBColor | None = None, line_w: int = 9525, radius: float | None = None):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(kind, left, top, width, height)
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if alpha is not None and alpha < 100:
        _set_alpha(shp, alpha)
    if line is None:
        _no_line(shp)
    else:
        shp.line.color.rgb = line
        shp.line.width = Emu(line_w)
    _no_shadow(shp)
    if radius:
        # python-pptx exposes the adjustment as a fraction of the short side.
        try:
            shp.adjustments[0] = radius
        except Exception:  # noqa: BLE001
            pass
    return shp


def _set_alpha(color, pct: int) -> None:
    """Set solid-fill transparency (0 transparent, 100 opaque)."""
    shape = color if hasattr(color, "_element") else None
    if shape is None:
        return
    solid = shape._element.spPr.find(qn("a:solidFill"))
    if solid is None:
        return
    srgb = solid.find(qn("a:srgbClr"))
    if srgb is None:
        return
    srgb.append(srgb.makeelement(qn("a:alpha"), {"val": str(int(pct * 1000))}))


def add_gradient(slide, left, top, width, height, c1: RGBColor, c2: RGBColor,
                 angle: int = 45, alpha1: int = 100, alpha2: int = 100):
    """A two-stop linear gradient. python-pptx has no public API for this."""
    shp = add_rect(slide, left, top, width, height, c1)
    spPr = shp._element.spPr
    for tag in ("a:solidFill", "a:noFill", "a:gradFill"):
        el = spPr.find(qn(tag))
        if el is not None:
            spPr.remove(el)
    grad = spPr.makeelement(qn("a:gradFill"), {"flip": "none", "rotWithShape": "1"})
    lst = grad.makeelement(qn("a:gsLst"), {})
    for pos, color, alpha in ((0, c1, alpha1), (100000, c2, alpha2)):
        gs = lst.makeelement(qn("a:gs"), {"pos": str(pos)})
        clr = gs.makeelement(qn("a:srgbClr"), {"val": str(color)})
        if alpha < 100:
            clr.append(clr.makeelement(qn("a:alpha"), {"val": str(int(alpha * 1000))}))
        gs.append(clr)
        lst.append(gs)
    grad.append(lst)
    grad.append(grad.makeelement(qn("a:lin"), {"ang": str(int(angle * 60000)), "scaled": "0"}))
    # gradFill must sit before a:ln in spPr
    ln = spPr.find(qn("a:ln"))
    if ln is not None:
        ln.addprevious(grad)
    else:
        spPr.append(grad)
    return shp


def add_line(slide, x1, y1, x2, y2, color: RGBColor, width: int = 12700):
    from pptx.enum.shapes import MSO_CONNECTOR
    shp = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
    shp.line.color.rgb = color
    shp.line.width = Emu(width)
    return shp


def _set_run_font(run, size, color, bold=False, latin="Segoe UI", cjk="Microsoft YaHei",
                  italic=False, spacing: float | None = None) -> None:
    f = run.font
    f.size = size
    f.bold = bold
    f.italic = italic
    f.name = latin
    f.color.rgb = color
    rpr = run._r.get_or_add_rPr()
    for tag, face in (("a:ea", cjk), ("a:cs", cjk)):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", face)
    if spacing is not None:
        rpr.set("spc", str(int(spacing * 100)))


def add_text(slide, left, top, width, height, text: str, theme: Theme, *,
             size=Pt(14), color: RGBColor | None = None, bold=False, align="left",
             anchor="top", line_spacing=1.25, spacing: float | None = None,
             italic=False, font: str | None = None, wrap: bool = True):
    color = color if color is not None else theme.rgb("ink")
    if font is None:
        latin, cjk = theme.body_latin, theme.body_cjk
    elif font == "headline":
        latin, cjk = theme.headline_latin, theme.headline_cjk
    elif font == "serif":
        latin, cjk = "Georgia", "Source Han Serif SC"
    else:
        latin = cjk = font
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = wrap
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
                          "bottom": MSO_ANCHOR.BOTTOM}[anchor]
    for i, line in enumerate(str(text).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                       "right": PP_ALIGN.RIGHT}[align]
        p.line_spacing = line_spacing
        run = p.add_run()
        run.text = line
        _set_run_font(run, size, color, bold, latin, cjk, italic, spacing)
    return box


def add_rich(slide, left, top, width, height, runs: list[tuple[str, dict]], theme: Theme,
             *, align="left", anchor="top", line_spacing=1.25):
    """One paragraph from styled runs: [("text", {"size": Pt(14), "bold": True}), ...]."""
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
                          "bottom": MSO_ANCHOR.BOTTOM}[anchor]
    p = tf.paragraphs[0]
    p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                   "right": PP_ALIGN.RIGHT}[align]
    p.line_spacing = line_spacing
    for text, spec in runs:
        run = p.add_run()
        run.text = text
        _set_run_font(
            run,
            spec.get("size", Pt(14)),
            spec.get("color", theme.rgb("ink")),
            spec.get("bold", False),
            spec.get("latin", theme.body_latin),
            spec.get("cjk", theme.body_cjk),
            spec.get("italic", False),
            spec.get("spacing"),
        )
    return box


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------


def text_width_pt(text: str, size_pt: float) -> float:
    """Approximate rendered width in points.

    Latin glyphs average 0.52em, CJK glyphs 1.0em, digits 0.56em. This is
    deliberately slightly generous: underestimating produces overflow, which
    is the one failure the renderer cannot repair later.
    """
    total = 0.0
    for ch in text:
        o = ord(ch)
        if o >= 0x2E80:          # CJK, full-width punctuation
            total += 1.0
        elif ch in " .,:;'|!":
            total += 0.28
        elif ch.isupper():
            total += 0.62
        elif ch.isdigit():
            total += 0.56
        else:
            total += 0.53
    return total * size_pt


def estimate_lines(text: str, width: Emu, size_pt: float) -> int:
    if not text:
        return 1
    usable = float(width) / 12700.0
    total = 0
    for para in str(text).split("\n"):
        w = text_width_pt(para, size_pt)
        total += max(1, math.ceil(w / max(usable, 1.0)))
    return max(1, total)


def fit_size(text: str, width: Emu, max_pt: float, min_pt: float) -> Pt:
    size = max_pt
    while size > min_pt:
        if text_width_pt(text, size) <= float(width) / 12700.0:
            break
        size -= 1
    return Pt(size)


def block_height(text: str, width: Emu, size_pt: float, line_spacing: float = 1.25) -> Emu:
    return Emu(int(estimate_lines(text, width, size_pt) * size_pt * line_spacing * 12700))


# --------------------------------------------------------------------------
# pictures
# --------------------------------------------------------------------------


def _cache_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), "deck-art")
    os.makedirs(d, exist_ok=True)
    return d


def trim_white(im: Image.Image, pad: int = 10) -> Image.Image:
    """Crop the near-white margin a chart screenshot arrives with.

    A matplotlib PNG saved with a white canvas carries 8–15% dead border on
    every side. Rendering it inside a panel then looks like a postage stamp.
    Only trims when the surviving area is still most of the image.
    """
    rgb = im.convert("RGB")
    w, h = rgb.size
    small = rgb.resize((min(w, 900), min(h, 900))) if max(w, h) > 900 else rgb
    px = small.load()
    sw, sh = small.size
    cols = [x for x in range(sw) if any(sum(px[x, y]) < 735 for y in range(0, sh, 2))]
    rows = [y for y in range(sh) if any(sum(px[x, y]) < 735 for x in range(0, sw, 2))]
    if not cols or not rows:
        return im
    fx, fy = w / sw, h / sh
    left, right = int(cols[0] * fx), int((cols[-1] + 1) * fx)
    top, bottom = int(rows[0] * fy), int((rows[-1] + 1) * fy)
    left, top = max(0, left - pad), max(0, top - pad)
    right, bottom = min(w, right + pad), min(h, bottom + pad)
    if (right - left) * (bottom - top) < 0.55 * w * h:
        return im
    return rgb.crop((left, top, right, bottom))


def prepare_picture(path: str, *, box_w: int, box_h: int, theme: Theme | None = None,
                    duotone: bool = False, grayscale: bool = False,
                    focus: tuple[float, float] = (0.5, 0.5), darken: float = 0.0,
                    radius: int = 0, trim: bool = False) -> str:
    """Crop to the box aspect, optionally duotone, return a cached PNG.

    Pre-cropping in PIL rather than using PowerPoint's crop attributes means
    the same asset looks identical in every viewer, including LibreOffice and
    the in-app preview.
    """
    src = Path(path)
    key = f"{src.stem}-{abs(hash((str(src), box_w, box_h, duotone, grayscale, focus, darken, radius, trim, theme.name if theme else '')))}"
    out = Path(_cache_dir()) / f"{key}.png"
    if out.exists() and out.stat().st_size > 1024:
        return str(out)

    im = Image.open(src).convert("RGB")
    if trim and not duotone and not grayscale:
        im = trim_white(im)
    iw, ih = im.size
    target = box_w / max(box_h, 1)
    have = iw / max(ih, 1)
    if have > target:                       # too wide: trim sides
        new_w = int(ih * target)
        x = int((iw - new_w) * focus[0])
        im = im.crop((x, 0, x + new_w, ih))
    elif have < target:                     # too tall: trim top/bottom
        new_h = int(iw / target)
        y = int((ih - new_h) * focus[1])
        im = im.crop((0, y, iw, y + new_h))

    im = im.resize((max(box_w, 2), max(box_h, 2)), Image.LANCZOS)
    if grayscale or duotone:
        im = ImageOps.grayscale(im).convert("RGB")
    if duotone and theme is not None:
        dark = theme.rgb("dark")
        accent = theme.rgb("accent")
        # A real duotone: shadows map to ink, highlights to a tint of accent.
        raw = tuple(int(str(x), 16) for x in (theme.dark.lstrip("#")[i:i + 2] for i in (0, 2, 4)))
        # Pure #070A0E shadows swallow a photograph: every midtone lands on
        # black and the cover looks like a gradient, not a picture. Lift the
        # shadow 10% toward the accent so shapes survive.
        shadow = tuple(int(c + (a - c) * 0.10) for c, a in zip(raw, accent))
        # Highlights stay close to the accent: pushing them 55% toward white
        # turned every photo into pale mint and killed the white type on top.
        light = tuple(min(255, int(c + (255 - c) * 0.42)) for c in
                      tuple(int(str(x), 16) for x in (theme.accent.lstrip("#")[i:i + 2] for i in (0, 2, 4))))
        # Autocontrast first, or a flat sky collapses into one solid blue and
        # the photo stops being a photo.
        gray = ImageOps.autocontrast(ImageOps.grayscale(im), cutoff=1)
        im = ImageOps.colorize(gray, black=shadow, white=light).convert("RGB")
    if darken:
        im = ImageEnhance.Brightness(im).enhance(max(0.0, 1.0 - darken))
    if radius:
        mask = Image.new("L", im.size, 0)
        from PIL import ImageDraw
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.size[0] - 1, im.size[1] - 1],
                                               radius=radius, fill=255)
        rgba = im.convert("RGBA")
        rgba.putalpha(mask)
        im = rgba
    im.save(out, "PNG")
    return str(out)


def add_picture(slide, path: str, left, top, width, height, theme: Theme | None = None, *,
                duotone=False, grayscale=False, focus=(0.5, 0.5), darken=0.0, radius=0,
                trim=False):
    px_w = max(2, int(float(width) / 914400 * 150))
    px_h = max(2, int(float(height) / 914400 * 150))
    prepared = prepare_picture(path, box_w=px_w, box_h=px_h, theme=theme,
                               duotone=duotone, grayscale=grayscale,
                               focus=focus, darken=darken, radius=radius, trim=trim)
    return slide.shapes.add_picture(prepared, left, top, width, height)


def picture_size(path: str) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def dominant(path: str, n: int = 4) -> list[tuple[int, int, int]]:
    """Palette of an image, quantised. Used to decide whether a picture is a
    photo, a screenshot, or a chart with a white field."""
    with Image.open(path) as im:
        small = im.convert("RGB").resize((80, 80))
        q = small.quantize(colors=max(2, n))
        pal = q.getpalette() or []
        counts = sorted(q.getcolors() or [], reverse=True)
        out = []
        for _, idx in counts[:n]:
            out.append(tuple(pal[idx * 3:idx * 3 + 3]))
        return out


def image_is_flat(path: str) -> bool:
    """True for a chart, logo, slide or screenshot — anything on a white field.

    Two signals, because either alone is wrong. A single dominant colour
    catches a logo on white; near-white coverage catches a chart whose bars
    and labels break up the field (the sample product-mix chart is 58% one
    colour but 78% near-white). Photographs essentially never clear 42%
    near-white pixels, so the test does not misclassify a bright sky.
    """
    with Image.open(path) as im:
        small = im.convert("RGB").resize((100, 100))
        px = list(small.getdata())
        near_white = sum(1 for r, g, b in px if r > 236 and g > 236 and b > 236)
        if near_white / len(px) > 0.42:
            return True
        cols = small.getcolors(100 * 100) or []
        if not cols:
            return False
        cols.sort(reverse=True)
        return cols[0][0] / (100 * 100) > 0.70


def image_brightness(path: str) -> float:
    with Image.open(path) as im:
        g = im.convert("L").resize((60, 60))
        px = list(g.getdata())
        return sum(px) / (len(px) * 255)


# --------------------------------------------------------------------------
# page furniture
# --------------------------------------------------------------------------


def page_number(slide, idx: int, theme: Theme, total: int | None = None,
                on_dark: bool = False, x=None) -> None:
    color = theme.rgb("dark_muted") if on_dark else theme.rgb("muted")
    label = f"{idx:02d}" if not total else f"{idx:02d} / {total:02d}"
    left = colx(11) if x is None else x
    add_text(slide, left, FOOT_TOP, Emu(int(col(1)) + int(GUTTER)), Inches(0.22),
             label, theme, size=Pt(9), color=color, align="right", spacing=0.8)


def source_note(slide, text: str, theme: Theme, on_dark: bool = False) -> None:
    if not text:
        return
    color = theme.rgb("dark_muted") if on_dark else theme.rgb("muted")
    add_text(slide, MARGIN_X, FOOT_TOP, col(9), Inches(0.22), text, theme,
             size=Pt(8.5), color=color, spacing=0.3)


def kicker(slide, text: str, theme: Theme, *, top=None, color=None, on_dark=False):
    if not text:
        return
    color = color or (theme.rgb("dark_muted") if on_dark else theme.rgb("accent"))
    add_text(slide, MARGIN_X, top or Inches(0.30), col(9), Inches(0.24),
             text.upper() if text.isascii() else text, theme, size=Pt(9.5),
             color=color, bold=True, spacing=2.2)


def footer_rule(slide, theme: Theme, on_dark: bool = False) -> None:
    color = theme.rgb("dark_muted") if on_dark else theme.rgb("hair")
    add_rect(slide, MARGIN_X, FOOT_TOP - Inches(0.14), CONTENT_W, Emu(6350), color)

def content_path(path: str) -> tuple[str, int, int]:
    """Path to a white-margin-trimmed copy plus its pixel size (cached)."""
    src = Path(path)
    out = Path(_cache_dir()) / f"{src.stem}-trim-{abs(hash(str(src)))}.png"
    if out.exists() and out.stat().st_size > 1024:
        with Image.open(out) as im:
            return str(out), im.size[0], im.size[1]
    with Image.open(src) as im:
        trimmed = trim_white(im.convert("RGB"))
        trimmed.save(out, "PNG")
        return str(out), trimmed.size[0], trimmed.size[1]

def add_scrim(slide, left, top, width, height, color: RGBColor,
              stops: list[tuple[float, float]], angle: int = 0):
    """Multi-stop gradient of one colour — a scrim, not a colour transition.

    `stops` is [(position 0-100, alpha 0-100), ...]. A flat left half needs two
    stops at alpha 100: a solid block butted against a two-stop gradient leaves
    a visible seam once the renderer bands it.
    """
    shp = add_rect(slide, left, top, width, height, color)
    spPr = shp._element.spPr
    for tag in ("a:solidFill", "a:noFill", "a:gradFill"):
        el = spPr.find(qn(tag))
        if el is not None:
            spPr.remove(el)
    grad = spPr.makeelement(qn("a:gradFill"), {"flip": "none", "rotWithShape": "1"})
    lst = grad.makeelement(qn("a:gsLst"), {})
    for pos, alpha in stops:
        gs = lst.makeelement(qn("a:gs"), {"pos": str(int(pos * 1000))})
        clr = gs.makeelement(qn("a:srgbClr"), {"val": str(color)})
        clr.append(clr.makeelement(qn("a:alpha"), {"val": str(int(alpha * 1000))}))
        gs.append(clr)
        lst.append(gs)
    grad.append(lst)
    grad.append(grad.makeelement(qn("a:lin"), {"ang": str(int(angle * 60000)), "scaled": "0"}))
    ln = spPr.find(qn("a:ln"))
    if ln is not None:
        ln.addprevious(grad)
    else:
        spPr.append(grad)
    return shp

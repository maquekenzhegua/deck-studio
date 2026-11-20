"""Turn a deck the user already owns into a theme we can render with.

Why: every company already has a colour scheme, and "make it look like ours"
is the first thing anyone asks a slide tool.  Reading `theme1.xml` gives us the
real scheme — not a guess from a screenshot — so the output can inherit the
brand without copying anyone's layout.

The mapping is deliberately conservative: colours are *chosen by role*
(background, ink, accent), never dumped in order, because a deck's accent2 is
usually a chart colour and makes a terrible page accent.
"""

from __future__ import annotations

from .visual import Theme

# ---------------------------------------------------------------- colour math


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02X}" for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    """t=0 → a, t=1 → b."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(ra[i] + (rb[i] - ra[i]) * t for i in range(3)))


def luma(hex_color: str) -> float:
    """Perceived brightness, 0–1. The only number contrast decisions need."""
    r, g, b = (c / 255 for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luma(a), luma(b)

    def f(x: float) -> float:
        return (x + 0.05) ** 2  # cheap stand-in for the sRGB curve, monotone

    hi, lo = max(la, lb), min(la, lb)
    return (f(hi) + 0.05) / (f(lo) + 0.05)


def saturation(hex_color: str) -> float:
    r, g, b = _rgb(hex_color)
    hi, lo = max(r, g, b), min(r, g, b)
    return 0.0 if hi == 0 else (hi - lo) / hi


SERIF_HINTS = ("georgia", "times", "garamond", "cambria", "palatino", "book",
               "song", "serif", "宋", "仿宋", "楷")


def _looks_serif(name: str) -> bool:
    low = (name or "").lower()
    return any(h in low for h in SERIF_HINTS)


# ---------------------------------------------------------------- role picking


def _clean(colors: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in (colors or {}).items():
        if isinstance(v, str) and v.startswith("#") and len(v) in (4, 7):
            out[k.lower()] = v.upper() if len(v) == 7 else v
    return out


def build(colors: dict[str, str], fonts: dict | None = None,
          *, name: str = "custom", label: str = "你的模板",
          title_pt: float = 0.0) -> Theme:
    """Map a DrawingML colour scheme onto our six roles."""
    c = _clean(colors)
    # DrawingML is explicit about roles: lt1 is the page, dk1 is the type.
    # A template with dk1=white and lt1=near-black is a *dark* deck, not a
    # mistake to be "corrected" into a light one.
    paper = c.get("lt1", "#FFFFFF")
    ink = c.get("dk1", "#111111")
    if contrast(paper, ink) < 4.5:
        ink = "#F5F7FA" if luma(paper) < 0.5 else "#111111"
    if luma(paper) < 0.35:
        paper = mix(paper, "#000000", 0.10)

    # Accents: keep only ones that read against the page, rank by contrast and
    # then by chroma — a grey "accent" is not an accent.
    pool = [(k, v) for k, v in c.items() if k.startswith("accent")]
    scored = []
    for k, v in pool:
        cr = contrast(v, paper)
        if cr >= 3.0:
            scored.append((cr * (0.45 + saturation(v)), k, v))
    scored.sort(reverse=True)
    # Office convention: accent1 is the brand colour. Only outrank it when it
    # cannot be read on the page — a red accent4 is not a brand just because
    # it is more saturated.
    first = c.get("accent1", "")
    if first and contrast(first, paper) >= 3.0:
        accent = first
    else:
        accent = scored[0][2] if scored else mix(ink, "#2F6FED", 0.55)
    accent2 = next((v for _, k, v in scored if v.upper() != accent.upper()),
                   mix(accent, ink, 0.45))

    # A dark page needs a darker *page* for the cover, not a darker text
    # colour — mixing white ink toward black gives a grey that reads as a bug.
    base = paper if luma(paper) < 0.5 else ink
    dark = mix(base, "#000000", 0.30)
    dark = mix(dark, accent, 0.08)
    dark_ink = "#FFFFFF" if contrast("#FFFFFF", dark) > 3.2 else "#0B0B0C"
    dark_muted = mix(dark, dark_ink, 0.62)

    soft = mix(paper, accent, 0.08)
    hair = mix(paper, ink, 0.16)
    muted = mix(ink, paper, 0.42)

    major = ((fonts or {}).get("majorFont") or {})
    latin = (major.get("latin") or "").strip()
    cjk = (major.get("ea") or "").strip()
    serif = _looks_serif(latin) or _looks_serif(cjk)
    # Only adopt a typeface we can actually see on this machine; a missing font
    # silently falls back to something worse than our defaults.
    from .visual import _installed  # local import: avoids a cycle at import time

    headline_latin = latin if latin and _installed(latin) else ("Georgia" if serif else "Segoe UI")
    headline_cjk = cjk if cjk and _installed(cjk) else "Microsoft YaHei"

    head_pt = max(24.0, min(33.0, round(title_pt * 0.68))) if title_pt else 0.0
    return Theme(
        name=name, label=label,
        paper=paper, ink=ink, ink2=mix(ink, paper, 0.22), muted=muted,
        hair=hair, soft=soft, accent=accent, accent2=accent2,
        dark=dark, dark_ink=dark_ink, dark_muted=dark_muted,
        neg=accent2,
        headline_latin=headline_latin, headline_cjk=headline_cjk,
        body_latin="Segoe UI", body_cjk="Microsoft YaHei",
        serif_headline=serif,
        head_pt=head_pt,
    )

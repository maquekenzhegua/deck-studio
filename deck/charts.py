"""Charts, rendered as images and placed like photographs.

We do not use python-pptx's native charts. Two reasons:

1. Native charts inherit the deck theme, and every deck tool's default theme is
   what makes output look like 2013 clip art. Full control beats inheritance.
2. A matplotlib PNG has no editable data behind it. That sounds like a downside
   until you remember the numbers came from `compute.py` — the picture is a
   rendering of a fact, not a second source of truth.

The style is deliberately austere: no gridlines except a faint baseline, no
legend (there is one series), values written on the marks.
"""

from __future__ import annotations

import os
import tempfile

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from .theme import ACCENT, HAIRLINE, INK, INK_2, MUTED

_CJK = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "PingFang SC", "Source Han Sans SC"]


def _setup_font() -> None:
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 220
    plt.rcParams["savefig.dpi"] = 220


_setup_font()

ACCENT_HEX = "#%02X%02X%02X" % (ACCENT[0], ACCENT[1], ACCENT[2])
INK_HEX = "#%02X%02X%02X" % (INK[0], INK[1], INK[2])
INK2_HEX = "#%02X%02X%02X" % (INK_2[0], INK_2[1], INK_2[2])
MUTED_HEX = "#%02X%02X%02X" % (MUTED[0], MUTED[1], MUTED[2])
HAIR_HEX = "#%02X%02X%02X" % (HAIRLINE[0], HAIRLINE[1], HAIRLINE[2])


def _clean(ax) -> None:
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(HAIR_HEX)
    ax.spines["bottom"].set_linewidth(0.9)
    ax.tick_params(colors=MUTED_HEX, labelsize=9, length=0)
    ax.set_facecolor("white")


def _fmt_axis(v: float) -> str:
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:.1f}亿"
    if a >= 1e4:
        return f"{v / 1e4:.0f}万"
    if a >= 1000:
        return f"{v:,.0f}"
    return f"{v:g}"


def _labeler(unit: str | None):
    """Pick the number format from the column's unit.

    Without this a growth-rate chart prints `0.34` where the slide says
    `34.0%` — the same fact, two different-looking numbers, and the reader
    stops trusting both.
    """
    if unit == "ratio":
        def pct(v: float) -> str:
            return f"{v * 100:.1f}%"
        return pct
    return _fmt_axis


def _out(name: str) -> str:
    d = os.path.join(tempfile.gettempdir(), "deck-charts")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{name}.png")


def line(points: list[tuple[str, float]], name: str, *, w: float = 6.4, h: float = 3.2,
         fmt=None) -> str:
    """A single-series trend. Endpoints are labelled; the middle is not."""
    fmt = fmt or _fmt_axis
    labels = [p[0] for p in points]
    vals = [p[1] for p in points]
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor("white")
    xs = list(range(len(vals)))
    ax.plot(xs, vals, color=ACCENT_HEX, linewidth=2.6, solid_capstyle="round", zorder=3)
    ax.fill_between(xs, vals, min(vals) * 0.98 if min(vals) > 0 else 0,
                    color=ACCENT_HEX, alpha=0.07, zorder=1)
    ax.scatter([xs[0], xs[-1]], [vals[0], vals[-1]], s=26, color=ACCENT_HEX, zorder=4)
    for i in (0, len(vals) - 1):
        ax.annotate(
            fmt(vals[i]),
            (xs[i], vals[i]),
            textcoords="offset points",
            xytext=(0, 11 if i == len(vals) - 1 else 11),
            ha="right" if i == len(vals) - 1 else "left",
            fontsize=9.5,
            color=INK_HEX,
            fontweight="600",
        )
    step = max(1, len(labels) // 8)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels(labels[::step])
    ax.set_yticks([])
    ax.margins(x=0.02, y=0.18)
    _clean(ax)
    fig.tight_layout(pad=0.4)
    p = _out(f"line-{name}")
    fig.savefig(p, facecolor="white")
    plt.close(fig)
    return p


def bar(points: list[tuple[str, float]], name: str, *, w: float = 6.4, h: float = 3.2,
        fmt=None) -> str:
    """Horizontal bars, sorted, value written at the end of each bar.

    Horizontal because Chinese category names are short and wide; rotated
    x-labels are the fastest way to look generated.
    """
    fmt = fmt or _fmt_axis
    pts = sorted(points, key=lambda kv: kv[1])
    labels = [p[0] for p in pts]
    vals = [p[1] for p in pts]
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor("white")
    ys = list(range(len(vals)))
    colors = [ACCENT_HEX if i == len(vals) - 1 else "#C9D2E3" for i in ys]
    ax.barh(ys, vals, color=colors, height=0.62, zorder=3)
    neg = any(v < 0 for v in vals)
    for y, v in zip(ys, vals):
        # A negative bar grows leftwards, so its label has to sit outside the
        # left end or it lands on top of the bar.
        ax.annotate(
            fmt(v),
            (v, y),
            textcoords="offset points",
            xytext=(-8, 0) if v < 0 else (8, 0),
            ha="right" if v < 0 else "left",
            va="center",
            fontsize=9,
            color=INK2_HEX,
        )
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=9.5, color=INK2_HEX)
    ax.set_xticks([])
    ax.margins(x=0.16, y=0.06)
    _clean(ax)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_visible(False)
    if neg:
        # The zero line is the only reference a signed bar chart needs.
        ax.axvline(0, color=HAIR_HEX, linewidth=0.9, zorder=2)
    fig.tight_layout(pad=0.4)
    p = _out(f"bar-{name}")
    fig.savefig(p, facecolor="white")
    plt.close(fig)
    return p


def donut(points: list[tuple[str, float]], name: str, *, w: float = 4.4, h: float = 4.4) -> str:
    """Composition. At most five slices; the rest is already folded into 其他."""
    pts = sorted(points, key=lambda kv: -kv[1])[:5]
    vals = [p[1] for p in pts]
    labels = [p[0] for p in pts]
    shades = ["#1F5AFF", "#8FA8FF", "#C9D2E3", "#E3E6EB", "#F2F4F7"]
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor("white")
    wedges, _ = ax.pie(
        vals,
        colors=shades[: len(vals)],
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.32, "edgecolor": "white", "linewidth": 2},
    )
    total = sum(vals) or 1
    ax.text(0, 0.08, f"{len(pts)}", ha="center", va="center", fontsize=22, color=INK_HEX, fontweight="700")
    ax.text(0, -0.22, "类", ha="center", va="center", fontsize=9, color=MUTED_HEX)
    ax.legend(
        wedges,
        [f"{l}  {v / total * 100:.0f}%" for l, v in zip(labels, vals)],
        loc="center left",
        bbox_to_anchor=(0.98, 0.5),
        frameon=False,
        fontsize=9.5,
    )
    ax.set_aspect("equal")
    fig.tight_layout(pad=0.2)
    p = _out(f"donut-{name}")
    fig.savefig(p, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return p


def render(fact, name: str, *, w: float = 6.4, h: float = 3.2) -> str | None:
    if not fact.points:
        return None
    fmt = _labeler(getattr(fact, "unit", None))
    if fact.chart == "line":
        return line(fact.points, name, w=w, h=h, fmt=fmt)
    if fact.chart == "bar":
        return bar(fact.points, name, w=w, h=h, fmt=fmt)
    if fact.chart == "pie":
        return donut(fact.points, name, w=min(w, h), h=min(w, h))
    return None

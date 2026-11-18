"""Charts, rendered as images and placed like photographs.

We do not use python-pptx's native charts. Two reasons:

1. Native charts inherit the deck theme, and every deck tool's default theme is
   what makes output look like 2013 clip art. Full control beats inheritance.
2. A matplotlib PNG has no editable data behind it. That sounds like a downside
   until you remember the numbers came from `compute.py` — the picture is a
   rendering of a fact, not a second source of truth.

House style, taken from how consulting decks actually draw charts:

* **No chart title.** The slide headline states the claim; the chart proves it.
  Two titles on one page is the loudest tell that a machine assembled it.
* **The unit appears once**, small and grey, at the top left — never repeated on
  every label and never implied.
* **One colour carries the message**, everything else is grey. A chart where
  every bar has its own colour asks the reader to compare all of them.
* **Thin marks.** Fat bars read as decoration; 0.55 of the row height reads as
  a measurement.
* **No y-axis, no gridlines** for bars. The value is written at the end of the
  bar, which is the only reference needed.
"""

from __future__ import annotations

import os
import tempfile

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter

from .theme import ALERT, HAIRLINE, INK, INK_2, MUTED, PRIMARY, PRIMARY_MID

_CJK = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "PingFang SC", "Source Han Sans SC"]


def _setup_font() -> None:
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 240
    plt.rcParams["savefig.dpi"] = 240


_setup_font()


def _hex(c) -> str:
    return "#%02X%02X%02X" % (c[0], c[1], c[2])


PRIMARY_HEX = _hex(PRIMARY)
MID_HEX = _hex(PRIMARY_MID)
ALERT_HEX = _hex(ALERT)
INK_HEX = _hex(INK)
INK2_HEX = _hex(INK_2)
MUTED_HEX = _hex(MUTED)
HAIR_HEX = _hex(HAIRLINE)
GREY_HEX = "#C3CAD4"   # context marks: present but not arguing


def _out(name: str) -> str:
    d = os.path.join(tempfile.gettempdir(), "deck-charts")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{name}.png")


def _bare(ax) -> None:
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=MUTED_HEX, labelsize=11, length=0, pad=3)
    ax.set_facecolor("white")


def _unit_note(fig, unit: str | None, fmt) -> None:
    """State the unit once. A number without a unit is a question."""
    text = {"ratio": "占比", "money": "金额", "count": "数量", "raw": ""}.get(unit or "", "")
    if unit == "ratio":
        text = "%"
    if not text:
        return
    fig.text(0.012, 0.972, text, ha="left", va="top", fontsize=11, color=MUTED_HEX)


def _short(v: float) -> str:
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
    return _short


def line(points: list[tuple[str, float]], name: str, *, w: float = 6.4, h: float = 3.2,
         fmt=None, unit: str | None = None) -> str:
    """A single-series trend. Endpoints labelled, middle left to the eye."""
    fmt = fmt or _short
    labels = [p[0] for p in points]
    vals = [p[1] for p in points]
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor("white")
    xs = list(range(len(vals)))
    ax.plot(xs, vals, color=PRIMARY_HEX, linewidth=2.0, solid_capstyle="round", zorder=3)
    base = min(0.0, min(vals))
    ax.fill_between(xs, vals, base, color=PRIMARY_HEX, alpha=0.06, zorder=1, linewidth=0)
    for i in (0, len(vals) - 1):
        ax.scatter([xs[i]], [vals[i]], s=18, color=PRIMARY_HEX, zorder=4)
        ax.annotate(
            fmt(vals[i]),
            (xs[i], vals[i]),
            textcoords="offset points",
            xytext=(-2 if i == 0 else 2, 9),
            ha="left" if i == 0 else "right",
            fontsize=11, color=INK_HEX, fontweight="600",
        )
    step = max(1, len(labels) // 7)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels(labels[::step], fontsize=11, color=MUTED_HEX)
    ax.set_yticks([])
    ax.margins(x=0.04, y=0.22)
    ax.grid(axis="y", color=HAIR_HEX, linewidth=0.7, zorder=0)
    _bare(ax)
    _unit_note(fig, unit, fmt)
    fig.tight_layout(pad=0.5)
    p = _out(f"line-{name}")
    fig.savefig(p, facecolor="white")
    plt.close(fig)
    return p


def bar(points: list[tuple[str, float]], name: str, *, w: float = 6.4, h: float = 3.2,
        fmt=None, unit: str | None = None) -> str:
    """Horizontal bars, sorted, value written at the end of each bar.

    Horizontal because Chinese category names are short and wide; rotated
    x-labels are the fastest way to look generated.
    """
    fmt = fmt or _short
    pts = sorted(points, key=lambda kv: kv[1])
    labels = [p[0] for p in pts]
    vals = [p[1] for p in pts]
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor("white")
    ys = list(range(len(vals)))
    # The largest bar carries the argument; the rest are context.
    top = len(vals) - 1
    colors = []
    for i in ys:
        if vals[i] < 0:
            colors.append(ALERT_HEX)      # a negative is the problem, so it is red
        elif i == top:
            colors.append(PRIMARY_HEX)
        else:
            colors.append(GREY_HEX)
    ax.barh(ys, vals, color=colors, height=0.40, zorder=3)
    neg = any(v < 0 for v in vals)
    span = max(abs(v) for v in vals) or 1
    for y, v in zip(ys, vals):
        ax.annotate(
            fmt(v),
            (v, y),
            textcoords="offset points",
            xytext=(-7, 0) if v < 0 else (7, 0),
            ha="right" if v < 0 else "left",
            va="center",
            fontsize=11, color=INK2_HEX,
        )
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=11, color=INK_HEX)
    ax.set_xticks([])
    # A negative bar's label sits to the left of its end, which is where the
    # category names live. Without matching room on that side the two
    # overprint each other -- the label ends up on top of "东北".
    pad_l = 0.24 if neg else 0.02
    ax.set_xlim(min(0, min(vals)) - span * pad_l, max(vals) + span * 0.20)
    ax.set_ylim(-0.6, len(vals) - 0.4)
    _bare(ax)
    if neg:
        ax.axvline(0, color=MUTED_HEX, linewidth=0.9, zorder=2)
    _unit_note(fig, unit, fmt)
    fig.tight_layout(pad=0.5)
    p = _out(f"bar-{name}")
    fig.savefig(p, facecolor="white")
    plt.close(fig)
    return p


def donut(points: list[tuple[str, float]], name: str, *, w: float = 4.4, h: float = 4.4,
          fmt=None, unit: str | None = None) -> str:
    """Composition. At most five slices; the rest is already folded into 其他."""
    pts = sorted(points, key=lambda kv: -kv[1])[:5]
    vals = [p[1] for p in pts]
    labels = [p[0] for p in pts]
    shades = [PRIMARY_HEX, MID_HEX, "#9FB0C6", GREY_HEX, HAIR_HEX]
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor("white")
    wedges, _ = ax.pie(
        vals,
        colors=shades[: len(vals)],
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.30, "edgecolor": "white", "linewidth": 2},
    )
    total = sum(vals) or 1
    ax.legend(
        wedges,
        [f"{l}   {v / total * 100:.0f}%" for l, v in zip(labels, vals)],
        loc="center left", bbox_to_anchor=(0.96, 0.5),
        frameon=False, fontsize=11, labelcolor=INK_HEX,
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
    unit = getattr(fact, "unit", None)
    fmt = _labeler(unit)
    if fact.chart == "line":
        return line(fact.points, name, w=w, h=h, fmt=fmt, unit=unit)
    if fact.chart == "bar":
        return bar(fact.points, name, w=w, h=h, fmt=fmt, unit=unit)
    if fact.chart == "pie":
        return donut(fact.points, name, w=min(w, h), h=min(w, h), fmt=fmt, unit=unit)
    return None

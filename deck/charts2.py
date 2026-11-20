"""Charts v2: drawn for the page they sit on, not for a report.

The v1 charts were correct and timid: 11pt labels, a unit floating in the
corner, bars the colour of wet cardboard. Printed at slide size the text
projected to ~7pt, which is the visual equivalent of mumbling.

This module keeps the one rule that mattered (one colour carries the claim)
and fixes the rest:

* type sizes are set for projection, not for a 6-inch PNG;
* the figure is transparent, so it sits on the page's own paper instead of
  arriving with a white box around it;
* the value label is attached to the mark, not parked on an axis;
* the longest bar is the only one in accent; everything else recedes.
"""

from __future__ import annotations

import os
import tempfile

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.colors import to_rgb

from .visual import Theme

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
    plt.rcParams["text.antialiased"] = True


_setup_font()


def _out(name: str) -> str:
    d = os.path.join(tempfile.gettempdir(), "deck-charts2")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{name}.png")


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
    if unit == "ratio":
        def pct(v: float) -> str:
            return f"{v * 100:.1f}%"
        return pct
    return _short


def _tint(hex_color: str, amount: float, toward: str = "#FFFFFF") -> str:
    a = np.array(to_rgb(hex_color))
    b = np.array(to_rgb(toward))
    return "#%02X%02X%02X" % tuple(int(round(c * 255)) for c in (a + (b - a) * amount))


def _canvas(w: float, h: float):
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    return fig, ax


def _bare(ax, theme: Theme) -> None:
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=theme.muted, labelsize=11, length=0, pad=6)
    ax.set_facecolor("none")


def _unit_note(fig, unit: str | None) -> None:
    text = {"ratio": "%", "money": "", "count": "", "raw": ""}.get(unit or "", "")
    if not text:
        return
    fig.text(0.01, 0.985, text, ha="left", va="top", fontsize=10.5, color="#8A94A6")


def bar(points: list[tuple[str, float]], name: str, theme: Theme, *,
        w: float = 8.2, h: float = 4.3, fmt=None, unit: str | None = None) -> str:
    """Horizontal bars, sorted, value written against the end of each bar.

    Horizontal because Chinese category names are short and wide; rotated
    x-labels are the fastest way to look generated. Bars are thin so the row
    rhythm, not the fill, carries the page.
    """
    fmt = fmt or _labeler(unit)
    pts = sorted(points, key=lambda kv: kv[1])
    labels = [p[0] for p in pts]
    vals = [p[1] for p in pts]
    n = len(vals)
    fig, ax = _canvas(w, h)
    ys = np.arange(n)
    top = n - 1
    accent = theme.accent
    rest = _tint(theme.ink, 0.80, theme.paper)
    colors = []
    for i in ys:
        if vals[i] < 0:
            colors.append(theme.negative)
        elif i == top:
            colors.append(accent)
        else:
            colors.append(rest)
    height = min(0.58, 3.4 / max(n, 1))
    ax.barh(ys, vals, color=colors, height=height, zorder=3)

    neg = any(v < 0 for v in vals)
    span = max(abs(v) for v in vals) or 1
    fs = 13.5 if n <= 6 else (12.0 if n <= 9 else 11.0)
    for y, v in zip(ys, vals):
        # A negative bar grows leftwards, so its label goes just right of the
        # zero line: outside the bar, and never on top of the category name.
        ax.annotate(
            fmt(v), (0 if v < 0 else v, y),
            textcoords="offset points",
            xytext=(10 if v < 0 else 9, 0),
            ha="left",
            va="center",
            fontsize=fs,
            color=(theme.negative if v < 0
                   else (theme.ink if v == max(vals) else theme.ink2)),
            fontweight="600" if v == max(vals) else "normal",
        )
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=fs + 0.5, color=theme.ink)
    ax.set_xticks([])
    pad_l = 0.10 if neg else 0.03
    ax.set_xlim(min(0, min(vals)) - span * pad_l, max(vals) + span * 0.22)
    ax.set_ylim(-0.62, n - 0.38)
    _bare(ax, theme)
    if neg:
        ax.axvline(0, color=_tint(theme.ink, 0.55, theme.paper), linewidth=1.0, zorder=2)
    fig.tight_layout(pad=0.4)
    p = _out(f"bar-{name}")
    fig.savefig(p, transparent=True)
    plt.close(fig)
    return p


def line(points: list[tuple[str, float]], name: str, theme: Theme, *,
         w: float = 8.2, h: float = 4.3, fmt=None, unit: str | None = None) -> str:
    fmt = fmt or _labeler(unit)
    labels = [p[0] for p in points]
    vals = [p[1] for p in points]
    fig, ax = _canvas(w, h)
    xs = np.arange(len(vals))
    ax.plot(xs, vals, color=theme.accent, linewidth=2.6, solid_capstyle="round", zorder=3)
    base = min(0.0, min(vals))
    ax.fill_between(xs, vals, base, color=theme.accent, alpha=0.07, zorder=1, linewidth=0)
    # Label every point when there are few, otherwise the endpoints only.
    # Endpoint labels sit *beside* the dot, not above it: above collides with
    # the last segment whenever the series ends on a dip.
    few = len(vals) <= 6
    show = range(len(vals)) if few else (0, len(vals) - 1)
    for i in show:
        ax.scatter([xs[i]], [vals[i]], s=26, color=theme.accent, zorder=4,
                   edgecolor=theme.paper, linewidth=1.6)
        edge = (i == 0 or i == len(vals) - 1) and not few
        ax.annotate(
            fmt(vals[i]), (xs[i], vals[i]),
            textcoords="offset points",
            # Keep endpoint labels inside the canvas: the first one leans in
            # from the left, the last one in from the right.
            xytext=((0, 12) if not edge else (11, 0)),
            ha=("center" if not edge else "left"),
            va="center" if edge else "baseline",
            fontsize=12, color=theme.ink, fontweight="600",
        )
    # An interior peak is the one thing an endpoint-only chart hides.
    if len(vals) > 6:
        peak = int(np.argmax(vals))
        # A peak that barely beats the endpoint is noise, not a finding.
        if peak not in (0, len(vals) - 1) and vals[peak] > vals[-1] * 1.05:
            ax.scatter([xs[peak]], [vals[peak]], s=120, facecolor="none",
                       edgecolor=theme.accent, linewidth=1.2, alpha=0.55, zorder=5)
            ax.annotate("峰值 " + fmt(vals[peak]), (xs[peak], vals[peak]),
                        textcoords="offset points", xytext=(0, 17),
                        ha="center", fontsize=10.5, color=theme.accent,
                        fontweight="600", zorder=6)

    step = max(1, len(labels) // 7)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels(labels[::step], fontsize=12, color=theme.muted)
    ax.tick_params(axis="x", pad=3)
    ax.set_yticks([])
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.22 or max(abs(hi) * 0.1, 1.0)
    ax.set_ylim(lo - pad * 0.55, hi + pad)
    ax.set_xlim(-0.55, len(vals) - 0.05)
    ax.margins(y=0)
    ax.grid(axis="y", color=_tint(theme.ink, 0.86, theme.paper), linewidth=0.9, zorder=0)
    _bare(ax, theme)
    fig.tight_layout(pad=0.2)
    p = _out(f"line-{name}")
    fig.savefig(p, transparent=True)
    plt.close(fig)
    return p


def donut(points: list[tuple[str, float]], name: str, theme: Theme, *,
          w: float = 8.05, h: float = 4.4, fmt=None, unit: str | None = None) -> str:
    """A share, with its base in the hole.

    A ring alone says "one slice is big"; the number in the middle says big
    out of what, which is the part a room argues with.
    """
    fmt = fmt or _labeler(unit)
    pts = sorted(points, key=lambda kv: -abs(kv[1]))[:5]
    vals = [abs(p[1]) for p in pts]
    labels = [p[0] for p in pts]
    shades = [
        theme.accent,
        _tint(theme.accent, 0.30),
        _tint(theme.accent, 0.56),
        _tint(theme.ink, 0.70),
        _tint(theme.ink, 0.84),
    ]
    fig = plt.figure(figsize=(w, h))
    fig.patch.set_alpha(0)
    ax = fig.add_axes([0.02, 0.04, 0.52, 0.92])
    ax.set_facecolor("none")
    wedges, _ = ax.pie(
        vals, colors=shades[: len(vals)], startangle=90, counterclock=False,
        wedgeprops={"width": 0.30, "edgecolor": "none"},
    )
    total = sum(vals) or 1
    ax.text(0, 0.10, fmt(total), ha="center", va="center",
            fontsize=25, color=theme.ink, fontweight="600")
    ax.text(0, -0.20, "合计", ha="center", va="center",
            fontsize=11, color=theme.muted)
    ax.set_aspect("equal")
    leg = fig.legend(
        wedges,
        [f"{l}    {v / total * 100:.0f}%" for l, v in zip(labels, vals)],
        loc="center left", bbox_to_anchor=(0.56, 0.5),
        frameon=False, fontsize=14, labelcolor=theme.ink,
        handlelength=1.15, handleheight=1.15, borderaxespad=0.0, labelspacing=0.9,
    )
    for text in leg.get_texts():
        text.set_color(theme.ink2)
    p = _out(f"donut-{name}")
    fig.savefig(p, transparent=True)
    plt.close(fig)
    return p


def multiline(series: dict[str, list[tuple[str, float]]], name: str, theme: Theme, *,
              w: float = 8.2, h: float = 4.3, fmt=None, unit: str | None = None) -> str | None:
    """One small panel per category, sharing an x axis.

    A single line answers "did it go up". Three lines on one axis answer it
    badly — the smallest series flattens into the baseline. Stacked panels
    keep every series legible while the shared x axis keeps the comparison
    honest, which is what a portfolio slide is actually asking.
    """
    fmt = fmt or _labeler(unit)
    cats = [c for c, pts in series.items() if len(pts) >= 4]
    if len(cats) < 2:
        return None
    cats = cats[:4]
    n = len(cats)
    fig, axes = plt.subplots(n, 1, figsize=(w, h), sharex=True, squeeze=False)
    fig.patch.set_alpha(0)
    totals = {c: sum(v for _, v in series[c]) for c in cats}
    lead = max(totals, key=totals.get)
    # Panels must share one y scale. Per-panel autoscaling draws a 39万 series
    # as tall as a 143万 one, which quietly contradicts the sentence the slide
    # is making ("the gap widened"). Same scale, or the comparison is a lie.
    allv = [v for c in cats for _, v in series[c]]
    glo, ghi = min(allv), max(allv)
    gspan = (ghi - glo) or max(abs(ghi) * 0.12, 1.0)
    lo_y, hi_y = glo - gspan * 0.10, ghi + gspan * 0.18
    for i, cat in enumerate(cats):
        ax = axes[i][0]
        pts = series[cat]
        vals = [v for _, v in pts]
        xs = list(range(len(vals)))
        cold = _tint(theme.ink, 0.62, theme.paper)
        color = theme.accent if cat == lead else cold
        ax.fill_between(xs, lo_y, vals, color=_tint(color, 0.86, theme.paper), linewidth=0)
        ax.plot(xs, vals, color=color, linewidth=2.4 if cat == lead else 1.8,
                solid_capstyle="round", zorder=3)
        ax.scatter([xs[-1]], [vals[-1]], s=26, color=color, zorder=4, linewidths=0)
        ax.set_ylim(lo_y, hi_y)
        ax.set_xlim(-0.35, len(vals) - 0.45)
        for side in ("top", "right", "left", "bottom"):
            ax.spines[side].set_visible(False)
        ax.set_yticks([])
        ax.tick_params(axis="x", colors=theme.muted, labelsize=10, length=0, pad=2)
        ax.set_facecolor("none")
        ax.grid(False)
        # Category and latest value live inside the panel: at projection size a
        # legend forces the eye to travel for information that belongs here.
        ax.text(-0.012, 0.86, cat, transform=ax.transAxes, ha="right", va="center",
                fontsize=12.5, color=theme.ink if cat == lead else theme.ink2,
                fontweight="600" if cat == lead else "400")
        ax.text(1.004, 0.5, fmt(vals[-1]), transform=ax.transAxes, ha="left", va="center",
                fontsize=12, color=color, fontweight="600")
        if i == n - 1:
            step = max(1, len(vals) // 6)
            ax.set_xticks(xs[::step])
            ax.set_xticklabels([pts[j][0] for j in range(0, len(vals), step)])
        else:
            ax.set_xticks([])
    fig.subplots_adjust(left=0.235, right=0.885, top=0.97, bottom=0.10, hspace=0.34)
    path = _out(f"{name}-multi")
    fig.savefig(path, transparent=True)
    plt.close(fig)
    return path


def heatmap(rows: list[tuple[str, list[float]]], cols: list[str], name: str, theme: Theme, *,
            w: float = 8.2, h: float = 4.3, unit: str | list[str] = "raw") -> str | None:
    """A scorecard: categories down, measures across, one colour ramp.

    Four charts would take four pages to say what one matrix says in a glance:
    which region is strong on which measure. Values are normalised per column
    so a large money column does not erase a percentage column, and the number
    is printed so the reader never has to decode the colour.
    """
    if not rows or not cols or len(rows) < 3:
        return None
    units = [unit] * len(cols) if isinstance(unit, str) else list(unit)
    fig, ax = _canvas(w, h)
    matrix = np.array([vals for _, vals in rows], dtype=float)
    norm = np.zeros_like(matrix)
    accent_rgb = np.array(to_rgb(theme.accent))
    paper_rgb = np.array(to_rgb(theme.paper))
    neg_rgb = np.array(to_rgb(theme.negative))
    # A column that crosses zero gets a diverging ramp, not a min-max ramp:
    # colouring -12% paler than +2% says "small effect" when the sign is the
    # whole point. Everything else stays a single-hue ramp.
    diverge = np.zeros(matrix.shape[1], dtype=bool)
    for j in range(matrix.shape[1]):
        col = matrix[:, j]
        lo, hi = float(np.nanmin(col)), float(np.nanmax(col))
        if lo < 0:
            diverge[j] = True
            scale = max(abs(lo), abs(hi)) or 1.0
            norm[:, j] = np.abs(col) / scale
        else:
            norm[:, j] = (col - lo) / (hi - lo) if hi > lo else 0.5
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            t = 0.10 + 0.78 * norm[i, j]
            base = neg_rgb if (diverge[j] and matrix[i, j] < 0) else accent_rgb
            color = tuple(paper_rgb + (base - paper_rgb) * t)
            ax.add_patch(plt.Rectangle((j + 0.03, i + 0.06), 0.94, 0.88,
                                       facecolor=color, edgecolor="none"))
            dark = norm[i, j] > 0.55
            ax.text(j + 0.5, i + 0.52, _cell(matrix[i, j], units[j]),
                    ha="center", va="center", fontsize=12.5,
                    color="#FFFFFF" if dark else theme.ink,
                    fontweight="600" if dark else "400")
    for j, label in enumerate(cols):
        ax.text(j + 0.5, -0.24, label, ha="center", va="center",
                fontsize=11, color=theme.muted)
    for i, (label, _) in enumerate(rows):
        ax.text(-0.10, i + 0.5, label, ha="right", va="center",
                fontsize=12, color=theme.ink2)
    ax.set_xlim(-0.02, len(cols) + 0.02)
    ax.set_ylim(len(rows) + 0.05, -0.55)
    ax.axis("off")
    fig.subplots_adjust(left=0.16, right=0.99, top=0.99, bottom=0.02)
    path = _out(f"{name}-heat")
    fig.savefig(path, transparent=True)
    plt.close(fig)
    return path


def _cell(v: float, unit: str) -> str:
    if unit == "ratio":
        return f"{v * 100:.0f}%"
    return _short(v)



def render(fact, name: str, theme: Theme, *, w: float = 8.2, h: float = 4.3) -> str | None:
    if fact.chart == "multiline" and getattr(fact, "series", None):
        return multiline(fact.series, name, theme, w=w, h=h,
                         fmt=_labeler(getattr(fact, "unit", None)), unit=getattr(fact, "unit", None))
    if not fact.points:
        return None
    unit = getattr(fact, "unit", None)
    fmt = _labeler(unit)
    if fact.chart == "line":
        return line(fact.points, name, theme, w=w, h=h, fmt=fmt, unit=unit)
    if fact.chart == "bar":
        return bar(fact.points, name, theme, w=w, h=h, fmt=fmt, unit=unit)
    if fact.chart == "pie":
        return donut(fact.points, name, theme, w=min(w, h), h=min(w, h), fmt=fmt, unit=unit)
    if fact.chart == "multiline" and getattr(fact, "series", None):
        return multiline(fact.series, name, theme, w=w, h=h, fmt=fmt, unit=unit)
    return None

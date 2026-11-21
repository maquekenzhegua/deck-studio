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
        # Only the lead series gets an area: three grey blocks turn a set of
        # small panels into a wall and hide the very comparison they carry.
        if cat == lead:
            ax.fill_between(xs, lo_y, vals, color=_tint(color, 0.88, theme.paper),
                            linewidth=0)
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
        # Sit the category on its own line, not on the panel: with a shared y
        # scale the line's height *is* the value, and a label pinned to the
        # panel centre points at empty space for the small series.
        y_frac = (vals[-1] - lo_y) / (hi_y - lo_y)
        ax.text(-0.012, y_frac, cat, transform=ax.transAxes, ha="right", va="center",
                fontsize=12.5, color=theme.ink if cat == lead else theme.ink2,
                fontweight="600" if cat == lead else "400")
        ax.text(1.004, y_frac, fmt(vals[-1]), transform=ax.transAxes, ha="left",
                va="center", fontsize=12, color=color, fontweight="600")
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
    ink_rgb = np.array(to_rgb(theme.ink))
    # A column that crosses zero gets a diverging ramp, not a min-max ramp:
    # colouring -12% paler than +2% says "small effect" when the sign is the
    # whole point. Everything else stays a single-hue ramp.
    diverge = np.zeros(matrix.shape[1], dtype=bool)
    for j in range(matrix.shape[1]):
        col = matrix[:, j]
        lo, hi = float(np.nanmin(col)), float(np.nanmax(col))
        if lo < 0:
            # Normalise each sign against its own side. Sharing one scale with
            # +34% made -12% look like a rounding error; the whole point of the
            # column is that the two signs are different stories.
            diverge[j] = True
            pos = max([v for v in col if v > 0], default=0.0) or 1.0
            neg = abs(min([v for v in col if v < 0], default=0.0)) or 1.0
            norm[:, j] = [abs(v) / (neg if v < 0 else pos) for v in col]
        else:
            norm[:, j] = (col - lo) / (hi - lo) if hi > lo else 0.5
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            # A diverging column keeps a visible floor: -4% must not read as
            # "almost nothing", the sign is the finding.
            if diverge[j]:
                t = 0.14 + 0.64 * float(norm[i, j]) ** 0.9
            elif units[j] in ("count", "raw"):
                # A neutral column needs a floor: the smallest count used to be
                # 8% ink on paper, which reads as a blank cell rather than as a
                # low number.
                t = 0.32 + 0.50 * float(norm[i, j])
            else:
                t = 0.10 + 0.72 * norm[i, j] ** 1.1
            # Three metrics, three visual languages: money in the brand ramp,
            # growth diverging, a plain count in neutral ink. Painting every
            # column in accent made the grid look like one metric repeated.
            if diverge[j]:
                base = neg_rgb if matrix[i, j] < 0 else accent_rgb
            elif units[j] in ("count", "raw"):
                base = paper_rgb + (ink_rgb - paper_rgb) * 0.62
            else:
                base = accent_rgb
            color = tuple(paper_rgb + (base - paper_rgb) * t)
            ax.add_patch(plt.Rectangle((j + 0.03, i + 0.06), 0.94, 0.88,
                                       facecolor=color, edgecolor="none"))
            # Pick the text colour that wins on contrast, not a fixed
            # brightness cut-off. Mid cells are exactly where a threshold
            # guesses wrong and white type turns into a smudge.
            lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                   for c in color]
            lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
            dark = (1.05 / (lum + 0.05)) > ((lum + 0.05) / 0.058)
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



def waterfall(steps: list[tuple[str, float]], name: str, theme: Theme, *,
              w: float = 8.2, h: float = 4.3, unit: str | None = None) -> str:
    """Cumulative build: which categories produce the total change.

    A ranked bar chart answers "who is biggest"; this answers "where did the
    movement come from", which is the question a growth page actually asks.
    """
    fig = plt.figure(figsize=(w, h), dpi=200)
    ax = fig.add_subplot(111)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(_tint(theme.ink, 0.72, theme.paper))
    ax.tick_params(axis="y", length=0, labelsize=12, colors=theme.muted)
    # A cumulative bridge is read in 万, not in 1e6: the offset text is the
    # single most "generated" looking thing a matplotlib chart can carry.
    if unit == "ratio":
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v * 100:.0f}%"))
    else:
        ax.yaxis.set_major_formatter(plt.FuncFormatter(
            lambda v, _: f"{v / 10000:,.0f}万" if abs(v) >= 10000 else f"{v:,.0f}"))
    ax.tick_params(axis="x", length=0, labelsize=11.5, colors=theme.ink2, pad=6)
    ax.grid(axis="y", color=_tint(theme.ink, 0.86, theme.paper), linewidth=1.0, alpha=.9)
    ax.set_axisbelow(True)

    run = 0.0
    xs, labels = [], []
    span = max((abs(v) for _, v in steps), default=1.0) or 1.0
    # A 2% drag must still read as a bar, not a hairline; 1.2% of the span was
    # thin enough that the step looked broken at presentation size.
    floor_h = span * 0.022
    for i, (label, delta) in enumerate(steps):
        height = max(abs(delta), floor_h)
        bottom = run if delta >= 0 else run - height
        color = theme.accent if delta >= 0 else theme.negative
        ax.bar(i, height, bottom=bottom, width=0.56, color=color,
               zorder=3, linewidth=0)
        if delta >= 0:
            # A short step gets its label lifted clear of the dashed total line.
            lift = 13 if abs(delta) < span * 0.05 else 6
            anchor, offset, va = run + height, lift, "bottom"
        else:
            # Below the drop, clear of both the bar and the running total.
            anchor, offset, va = bottom, -10, "top"
        ax.annotate(("+" if delta > 0 else "−") + _plain(abs(delta), unit),
                    (i, anchor), xytext=(0, offset), textcoords="offset points",
                    ha="center", va=va, fontsize=11, color=color)
        if i:
            ax.plot([i - 1 + 0.28, i - 0.28], [run, run],
                    color=_tint(theme.ink, 0.62, theme.paper), linewidth=1.1,
                    linestyle=(0, (2, 2)), zorder=2)
        run += delta
        xs.append(i)
        labels.append(label)
    # The total is a bar of its own, in ink: it is the claim, not a category.
    ax.bar(len(steps), run, width=0.56, color=theme.ink, zorder=3, linewidth=0)
    fig.subplots_adjust(left=0.145, right=0.985, top=0.93, bottom=0.17)
    # The total is printed inside its own bar, in paper: a dark label on a
    # dark bar is the one thing that makes a bridge unreadable.
    ax.annotate(_plain(run, unit), (len(steps), run), xytext=(0, -9),
                textcoords="offset points", ha="center", va="top",
                fontsize=12, color=theme.paper, fontweight="bold")
    xs.append(len(steps))
    labels.append("合计")
    ax.axhline(0, color=_tint(theme.ink, 0.55, theme.paper), linewidth=1.1, zorder=2)

    ax.set_xticks(xs)
    ax.set_xticklabels(labels)
    lo = min(0.0, run)
    hi = max(run, max((s for _, s in steps), default=0.0))
    span = (hi - lo) or 1.0
    ax.set_ylim(lo - span * 0.14, hi + span * 0.20)
    ax.set_xlim(-0.6, len(steps) + 0.6)
    fig.tight_layout(pad=0.25)
    out = _out(name + "-wf")
    fig.savefig(out, transparent=True)
    plt.close(fig)
    return out


def _plain(v: float, unit: str | None) -> str:
    if unit == "ratio":
        return f"{v * 100:.1f}%"
    if unit == "money":
        # Values arrive in yuan. A bar labelled 2,479,462 is unreadable; 248万
        # is the number a person would say out loud.
        return f"{v / 10000:,.0f}万" if abs(v) >= 10000 else f"{v:,.0f}"
    if abs(v) >= 10000:
        return f"{v / 10000:.0f}万"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.1f}"


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

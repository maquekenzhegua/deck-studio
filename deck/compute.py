"""Compute: turn tables into numbered facts.

This is moat #2, and it is the whole reason the deck can be trusted.

A language model asked to "summarise this sales data" will produce a number
that looks right and is wrong. So the model never sees the raw numbers it is
supposed to report. Instead we compute here, deterministically, and hand the
model a list of *fact ids*. The planner may only cite an id. The renderer looks
the number up. The verifier proves every digit on a slide came from here.

`Fact.value` is the only string allowed to appear as a figure in the deck.

Aggregation is the subtle part. Summing a revenue column is right; summing a
conversion rate is nonsense. A column's name decides which one it gets, and
that decision is made once, here, instead of being left to whoever writes the
sentence.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import fmt
from .ingest import Bundle, Table

MONEY_HINT = re.compile(r"额|金额|收入|营收|成本|利润|价格|售价|支出|预算|销售|GMV|gmv|revenue|cost|price|amount", re.I)
COUNT_HINT = re.compile(r"数|量|单量|人数|次数|件数|台数|用户|订单|访问|点击|count|qty|volume|orders|users", re.I)
CONVERSION = re.compile(r"转化|conversion|convert", re.I)
RATIO_HINT = re.compile(r"率|比|占比|百分比|rate|ratio|share|pct|percent", re.I)
# Anything measured per-unit or averaged must never be summed.
MEAN_HINT = re.compile(r"平均|均值|单价|客单价|均价|天数|时长|周期|年龄|评分|得分|分数|人均")
# Money that arrives averaged still needs a currency sign: "客单价 5,024.5" is
# a number, "客单价 5,024.5 元" is a price. Kept separate from MONEY_HINT so
# that adding it cannot accidentally turn a sum column into a mean one.
MONEY_UNIT = re.compile(r"价|额|收入|营收|成本|利润|支出|预算|薪|工资|revenue|cost|price|amount", re.I)
ID_HINT = re.compile(r"^id$|编号|序号|代码|code$|_id$", re.I)


@dataclass
class Fact:
    id: str
    kind: str                  # series | breakdown | scalar
    label: str
    value: str
    raw: float
    source: str
    column: str = ""
    unit: str = "raw"          # money | count | ratio | raw
    agg: str = "sum"           # sum | mean
    chart: str | None = None   # line | bar | pie
    points: list[tuple[str, float]] = field(default_factory=list)
    change: float | None = None
    score: float = 0.0
    # The bare number, and the category it belongs to. `value` is what a
    # sentence should say ("华东 34.0%"); `number` is what a big-type slide
    # should print, with the category demoted to a label above it.
    number: str = ""
    top: str = ""

    def as_prompt(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "kind": self.kind, "label": self.label, "value": self.value}
        if self.change is not None:
            d["change"] = fmt.signed_pct(self.change)
        if self.points:
            d["top"] = [[str(k), round(v, 4)] for k, v in sorted(self.points, key=lambda kv: -kv[1])[:5]]
            # Spell out counts the model would otherwise have to infer and get
            # wrong: it wrote "华北是唯一下滑的大区" while -4.0% and -12.0% were
            # both on the page. The number of declining points is a fact, so it
            # belongs in the fact, not in the model's head.
            neg = [k for k, v in self.points if v < 0]
            pos = [k for k, v in self.points if v > 0]
            if neg and pos:
                d["signs"] = {
                    "negative": [str(k) for k in neg],
                    "positive": [str(k) for k in pos],
                    "note": f"{len(neg)} 项为负、{len(pos)} 项为正；不要用「唯一」「全部」这类词"
                            f"除非它真的成立",
                }
            steps = funnel_steps(self.points)
            if steps:
                # A funnel's absolute counts say nothing; the step conversions
                # do. Without them the model writes "末端转化最弱" from the
                # counts alone and the table contradicts the headline.
                d["funnel"] = [[nm, round(v, 4), f"转化 {r * 100:.1f}%"]
                               for nm, v, r in steps]
                d["bottleneck"] = _bottleneck_label(steps)
        return d


def funnel_steps(points: list[tuple[str, float]]) -> list[tuple[str, float, float]]:
    """(name, value, conversion-from-previous) when the sequence is a funnel.

    A funnel is a strictly shrinking run of positive counts -- stages, not
    categories. Detected from the data rather than declared, so a ranking that
    happens to descend is not mistaken for one.
    """
    vals = [v for _, v in points]
    if len(vals) < 3 or not all(v > 0 for v in vals):
        return []
    if not all(a > b for a, b in zip(vals, vals[1:])):
        return []
    out = [(points[0][0], points[0][1], 1.0)]
    for (nm, v), (_, prev) in zip(points[1:], points):
        out.append((nm, v, v / prev if prev else 0.0))
    return out


def _bottleneck_label(steps: list[tuple[str, float, float]]) -> str:
    nm, v, r = min(steps[1:], key=lambda st: st[2])
    return f"{nm} 阶段转化最低，{r * 100:.1f}%"


def _classify(col: str) -> tuple[str, str]:
    """(unit, aggregation). Name-based, and deliberately conservative."""
    if MEAN_HINT.search(col):
        return ("money" if MONEY_UNIT.search(col) else "raw", "mean")
    if RATIO_HINT.search(col):
        return ("ratio", "mean")
    if MONEY_HINT.search(col):
        return ("money", "sum")
    if COUNT_HINT.search(col):
        return ("count", "sum")
    return ("raw", "sum")


def _display(v: float, unit: str) -> str:
    if unit == "money":
        return fmt.money(v)
    if unit == "count":
        return fmt.num(v)
    if unit == "ratio":
        return fmt.pct(v)
    return fmt.num(v)


def _numeric_cols(df: pd.DataFrame) -> list[str]:
    out = []
    n = max(len(df), 1)
    for c in df.columns:
        s = df[c]
        if not pd.api.types.is_numeric_dtype(s):
            continue
        if s.notna().sum() < max(3, n * 0.5):
            continue
        if ID_HINT.search(str(c)):
            continue
        if s.nunique(dropna=True) <= 1:
            continue
        if s.nunique(dropna=True) == s.notna().sum() and pd.api.types.is_integer_dtype(s):
            if s.min() > 1900 and s.max() < 2100:
                continue  # a year column, not a measure
        out.append(str(c))
    return out


def _time_col(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            return str(c)
        if s.dtype == object or pd.api.types.is_string_dtype(s):
            sample = s.dropna().astype(str).head(40)
            if len(sample) < 3:
                continue
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            if parsed.notna().mean() >= 0.8:
                return str(c)
    return None


def _category_cols(df: pd.DataFrame, exclude: set[str]) -> list[str]:
    out = []
    n = max(len(df), 1)
    for c in df.columns:
        if str(c) in exclude:
            continue
        s = df[c]
        if not (s.dtype == object or isinstance(s.dtype, pd.CategoricalDtype) or pd.api.types.is_string_dtype(s)):
            continue
        k = s.nunique(dropna=True)
        if 2 <= k <= 15 and s.notna().sum() >= n * 0.6:
            out.append(str(c))
    return out


def _agg(series: pd.Series, how: str) -> float:
    return float(series.mean()) if how == "mean" else float(series.sum())


def _period_label(ts: pd.Timestamp, this_year: int) -> str:
    return f"{ts.month}月" if ts.year == this_year else f"{ts.year}-{ts.month:02d}"


def _series_fact(table: Table, tcol: str, vcol: str, unit: str, agg: str, idx: int) -> Fact | None:
    df = table.df[[tcol, vcol]].copy()
    df[tcol] = pd.to_datetime(df[tcol], errors="coerce", format="mixed")
    df = df.dropna()
    if len(df) < 4:
        return None
    g = df.groupby(tcol)[vcol].agg("mean" if agg == "mean" else "sum").sort_index()
    if len(g) > 36:
        g = g.resample("MS").agg("mean" if agg == "mean" else "sum").dropna()
    this_year = int(g.index[0].year)
    pts = [(_period_label(pd.Timestamp(k), this_year), float(v)) for k, v in g.items()]
    vals = [v for _, v in pts]
    if not any(vals) or max(vals) == min(vals):
        return None
    total = _agg(pd.Series(vals), agg)
    first, last = vals[0], vals[-1]
    change = (last - first) / abs(first) if first else None
    trend = abs(last - first) / (abs(sum(vals) / len(vals)) or 1)
    monotone = sum(1 for a, b in zip(vals, vals[1:]) if b >= a) / max(len(vals) - 1, 1)
    label = f"{vcol}（按时间{'，均值' if agg == 'mean' else ''}）"
    return Fact(
        id=f"f{idx}", kind="series", label=label, value=_display(total, unit), raw=total,
        source=f"{table.name} · {vcol}", column=vcol, unit=unit, agg=agg, chart="line",
        points=pts, change=change, score=min(trend, 2.5) + abs(monotone - 0.5) * 2.4,
        number=_display(total, unit),
    )


def _breakdown_fact(table: Table, ccol: str, vcol: str, unit: str, agg: str, idx: int) -> Fact | None:
    df = table.df[[ccol, vcol]].copy()
    df[ccol] = df[ccol].astype(str)
    df = df.dropna()
    if len(df) < 3:
        return None
    how = "mean" if agg == "mean" else "sum"
    # sort=False keeps the table's row order. pandas otherwise orders groups by
    # key, and for Chinese keys that is code point order -- 商机 before 线索 --
    # which destroys the stage sequence a funnel is made of.
    g_raw = df.groupby(ccol, sort=False)[vcol].agg(how)
    # Keep the table's own row order: sorting by size makes every sequence look
    # monotone, and monotonicity is exactly what a funnel check asks about.
    order = [float(v) for v in g_raw.values]
    g = g_raw.sort_values(ascending=False)
    if len(g) > 8:
        head = g.head(7)
        rest = float(g.iloc[7:].agg("mean" if how == "mean" else "sum"))
        rows = [(str(k), float(v)) for k, v in head.items()] + [("其他", rest)]
    else:
        rows = [(str(k), float(v)) for k, v in g.items()]
    vals = [v for _, v in rows]
    if max(vals) == min(vals):
        return None
    # The headline of a breakdown is its most extreme member, never the total.
    # A summed conversion rate is how decks end up claiming 227%.
    top_label, top_val = rows[0]
    if unit == "ratio" and top_val >= 0.995 and len(rows) > 1:
        # A ratio whose top member is 100% is the entry row of a funnel: the
        # first stage converts into itself by definition. Leading with it says
        # "everything is fine" and buries the actual finding, which is the
        # worst stage. Lead with the low end instead.
        top_label, top_val = rows[-1]
        note = "（最低）"
    else:
        note = ""
    share = abs(top_val) / (sum(abs(v) for v in vals) or 1)
    uniform = 1 / len(vals)
    # A funnel is a stage sequence, and a stage sequence is the only table that
    # carries a conversion column next to its counts. Monotonic decline alone is
    # not evidence: a regional sales ranking also declines, and calling it a
    # funnel makes the deck print "转化" over numbers that are not conversions.
    conv = next((c for c in table.df.columns if c != ccol and c != vcol
                 and isinstance(c, str) and CONVERSION.search(c)), None)
    is_funnel = (bool(conv) and vcol != conv and len(order) >= 3
                 and all(a > b for a, b in zip(order, order[1:])))
    return Fact(
        id=f"f{idx}", kind="breakdown",
        label=f"{vcol} 按 {ccol}{note}",
        value=f"{top_label} {_display(top_val, unit)}", raw=float(top_val),
        source=f"{table.name} · {vcol} 按 {ccol}", column=vcol, unit=unit, agg=how,
        chart="funnel" if is_funnel else "bar", points=rows,
        score=(share - uniform) * 3 + 0.4,
        number=_display(top_val, unit), top=top_label,
    )


def _scalar_fact(table: Table, vcol: str, unit: str, agg: str, idx: int) -> Fact | None:
    s = pd.to_numeric(table.df[vcol], errors="coerce").dropna()
    if len(s) < 3:
        return None
    v = _agg(s, agg)
    if not math.isfinite(v) or v == 0:
        return None
    suffix = "（均值）" if agg == "mean" else ""
    return Fact(
        id=f"f{idx}", kind="scalar", label=f"{vcol}{suffix}", value=_display(v, unit), raw=v,
        source=f"{table.name} · {vcol}", column=vcol, unit=unit, agg=agg, chart=None, score=0.5,
        number=_display(v, unit),
    )


def extract(bundle: Bundle) -> list[Fact]:
    """All facts, ranked. Callers take the top N for the deck."""
    facts: list[Fact] = []
    idx = 0
    for table in bundle.tables:
        df = table.df
        numerics = _numeric_cols(df)
        if not numerics:
            continue
        tcol = _time_col(df)
        cats = _category_cols(df, exclude=set(numerics) | ({tcol} if tcol else set()))

        for vcol in numerics:
            unit, agg = _classify(vcol)
            if tcol:
                idx += 1
                f = _series_fact(table, tcol, vcol, unit, agg, idx)
                if f:
                    facts.append(f)
                else:
                    idx -= 1
            if cats:
                idx += 1
                f = _breakdown_fact(table, cats[0], vcol, unit, agg, idx)
                if f:
                    facts.append(f)
                else:
                    idx -= 1
            idx += 1
            f = _scalar_fact(table, vcol, unit, agg, idx)
            if f:
                facts.append(f)
            else:
                idx -= 1

    # One fact per (kind, source column): a wide sheet must not flood the deck.
    seen: set[tuple[str, str, str]] = set()
    deduped = []
    for f in sorted(facts, key=lambda x: -x.score):
        key = (f.kind, f.source.split(" · ")[0], f.column)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return deduped


def pick(facts: list[Fact], n: int) -> list[Fact]:
    """Top n, varied: bounded per kind, and never two views of the same column."""
    out: list[Fact] = []
    budget = {"series": 3, "breakdown": 3, "scalar": 3}
    used_cols: set[str] = set()
    for f in sorted(facts, key=lambda x: -x.score):
        if len(out) >= n:
            break
        if budget.get(f.kind, 0) <= 0:
            continue
        if f.column and f.column in used_cols:
            continue
        budget[f.kind] -= 1
        used_cols.add(f.column)
        out.append(f)
    return out

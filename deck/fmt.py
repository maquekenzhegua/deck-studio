"""Number formatting.

A generated deck says "12843621.5" and a human says "1,284 万". This module is
the difference. It also keeps the *displayed* string attached to the raw value,
so the verifier can prove a number on a slide came from a computation.
"""

from __future__ import annotations


def _big(v: float) -> str | None:
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:,.2f} 亿"
    if a >= 1e4:
        return f"{v / 1e4:,.1f} 万"
    return None


def money(v: float) -> str:
    b = _big(v)
    return b if b is not None else f"{v:,.0f}"


def num(v: float) -> str:
    b = _big(v)
    if b is not None:
        return b
    if v == int(v):
        return f"{int(v):,}"
    if abs(v) >= 100:
        return f"{v:,.1f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def pct(v: float, digits: int = 1) -> str:
    return f"{v * 100:.{digits}f}%"


def signed_pct(v: float, digits: int = 1) -> str:
    return f"{'+' if v >= 0 else ''}{v * 100:.{digits}f}%"


def delta_phrase(v: float) -> str:
    """Turn a relative change into a word, so bullets read like a person."""
    if v > 0.30:
        return "大幅增长"
    if v > 0.08:
        return "稳步上升"
    if v > -0.08:
        return "基本持平"
    if v > -0.30:
        return "有所下滑"
    return "明显回落"

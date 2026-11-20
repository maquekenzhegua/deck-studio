"""Pick the chart that fits the sentence, not the one the model happened to name.

A model writing an outline sees the shape of the data ("six regions, one rate
each") but it does not reliably connect "一条产品线吃掉一半收入" to a ring, or
"增量几乎全部来自华东" to a bridge. Left alone it writes `chart` for every one
of them and the deck ends up with six bar charts.

The rule here is deliberately narrow: it only fires when the *title* claims a
shape that another chart states better, and only when the fact can actually
carry that chart. Everything else is left exactly as the model wrote it — this
is a referee, not a second author.
"""

from __future__ import annotations

from .compute import Fact

# "构成" claims a whole divided into parts -> ring.
COMPOSITION = ("占比", "构成", "份额", "吃掉", "占去", "结构", "组合", "分掉")
# "贡献" claims a starting value moved by named deltas -> bridge.
CONTRIBUTION = ("贡献", "拉动", "带动", "增量", "拖累", "扯后", "差距来源")
# "分化" claims several lines separating over time -> small multiples.
SPREAD = ("分化", "拉开", "跟上", "没有跟上", "加速", "快过", "慢于", "并不同步")


def choose(fact: Fact, title: str) -> str:
    """Return a variant, or "" when the slide should keep what it has.

    A variant is only offered when the fact can carry it: a ring of two
    numbers is a pie of half and half, and a bridge with two steps is a
    subtraction. Returning "" is the common case and the safe one.
    """
    text = title or ""
    points = list(fact.points or [])
    n = len(points)

    if fact.series and any(word in text for word in SPREAD):
        return "multi"
    if (any(word in text for word in COMPOSITION)
            and 3 <= n <= 5 and fact.unit in ("money", "count", "raw")):
        return "donut"
    if (any(word in text for word in CONTRIBUTION)
            and fact.unit == "money" and n >= 3):
        return "waterfall"
    return ""


def apply(plan, facts) -> tuple[object, list[str]]:
    """Rewrite `variant` where the title and the fact agree on a better chart."""
    index = facts if isinstance(facts, dict) else {f.id: f for f in facts}
    changed: list[str] = []
    for i, slide in enumerate(plan.slides, 1):
        if slide.layout != "chart":
            continue
        ids = [x.strip() for x in (slide.fact or "").split(",") if x.strip()]
        fact = index.get(ids[0]) if ids else None
        if fact is None:
            continue
        want = choose(fact, slide.title)
        if not want or want == slide.variant:
            continue
        if want == "waterfall" and len(ids) < 2:
            # A bridge needs a growth rate beside the size; without both
            # numbers the renderer would fall back to a bar anyway.
            continue
        slide.variant = want
        changed.append(f"第 {i} 页按论点形状换成{ {'donut': '占比环', 'multi': '分面折线', 'waterfall': '增量桥'}[want] }")
    return plan, changed

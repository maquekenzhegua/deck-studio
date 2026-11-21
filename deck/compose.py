"""Offline planner: a defensible deck without a model call.

The model path in `planner.py` writes better prose, but it needs an endpoint,
a key and a network. A desktop app cannot assume any of those, and a deck tool
that only works when a cloud model is reachable is not a tool — it is a demo.

This module writes the same *shape* of argument from the facts themselves:

* every title is a claim derived from a computed comparison, never a section
  label;
* every number still goes through `{{fN}}` or stays out of the copy entirely;
* page order is trend → scale → structure → efficiency → action, which is the
  order a reader needs, not the order the tables arrived in.

It is deliberately conservative: where the data cannot support a sentence, the
sentence is dropped rather than softened into filler.
"""

from __future__ import annotations

import re
from pathlib import Path

from .compute import Fact, funnel_steps
from .ingest import Bundle
from .plan import Plan, Slide


# --------------------------------------------------------------------------
# fact selection
# --------------------------------------------------------------------------


def _metric(f: Fact) -> str:
    """'销售额 按 区域' -> '销售额'; '客单价（均值）' -> '客单价'."""
    name = (f.column or f.label).split("按")[0].strip()
    for mark in ("（", "("):
        if mark in name:
            name = name.split(mark)[0].strip()
    return name or f.label


def _ranked(f: Fact) -> list[tuple[str, float]]:
    return sorted(f.points, key=lambda kv: -kv[1])


def _series(facts: list[Fact]) -> Fact | None:
    rows = [f for f in facts if f.chart == "line" and len(f.points) >= 4]
    if not rows:
        return None
    # Prefer the longest, most valuable series; money beats counts on a cover.
    return max(rows, key=lambda f: (len(f.points), f.unit == "money", f.score))


def _ratio(facts: list[Fact]) -> Fact | None:
    rows = [f for f in facts if f.points and f.unit == "ratio" and f.chart == "bar"]
    if not rows:
        return None
    mixed = [f for f in rows if any(v < 0 for _, v in f.points)
             and any(v > 0 for _, v in f.points)]
    return max(mixed or rows, key=lambda f: f.score)


def _breakdowns(facts: list[Fact], used: set[str], limit: int = 2) -> list[Fact]:
    rows = [f for f in facts if f.points and f.chart == "bar" and f.id not in used
            and f.unit in ("money", "count", "raw")
            and not any(k in f.label for k in ("停留", "天数", "时长", "周期", "耗时"))]
    rows.sort(key=lambda f: -f.score)
    out: list[Fact] = []
    for f in rows:
        if any(f.label == g.label for g in out):
            continue
        out.append(f)
        if len(out) == limit:
            break
    return out


def _multi(facts: list[Fact]) -> Fact | None:
    """The product-line panel: several series on one time axis."""
    cands = [f for f in facts if getattr(f, "series", None)
             and any(k in f.label for k in ("产品", "线", "品类"))]
    if not cands:
        cands = [f for f in facts if getattr(f, "series", None)]
    # Revenue is the panel a room argues about; orders and price are follow-ups.
    return max(cands, key=lambda f: (f.unit == "money", f.score), default=None)


def _funnel(facts: list[Fact]) -> Fact | None:
    rows = [f for f in facts if f.chart == "funnel" and len(f.points) >= 3]
    return max(rows, key=lambda f: f.score) if rows else None


def _duration(facts: list[Fact], used: set[str]) -> Fact | None:
    rows = [f for f in facts if f.points and f.id not in used and f.unit == "raw"
            and any(k in f.label for k in ("停留", "天数", "时长", "周期", "耗时"))]
    return max(rows, key=lambda f: f.score) if rows else None


def _scalar(facts: list[Fact], *keys: str) -> Fact | None:
    for f in facts:
        if f.kind == "scalar" and any(k in f.label for k in keys):
            return f
    return None


# --------------------------------------------------------------------------
# sentence helpers
# --------------------------------------------------------------------------


def _neg(f: Fact) -> list[str]:
    return [k for k, v in _ranked(f) if v < 0]


def _top(f: Fact) -> str:
    return _ranked(f)[0][0] if f.points else ""


def _bottom(f: Fact) -> str:
    return _ranked(f)[-1][0] if f.points else ""


def _val(f: Fact | None, name: str) -> float:
    if f is None:
        return 0.0
    for k, v in f.points:
        if k == name:
            return v
    return 0.0


def _waterfall(facts: list[Fact]) -> Slide | None:
    """Growth, split by who produced it — sales and growth are two facts."""
    money = next((f for f in facts if f.points and f.unit == "money"
                  and "区域" in f.label), None)
    ratio = next((f for f in facts if f.points and f.unit == "ratio"
                  and "区域" in f.label), None)
    if not money or not ratio:
        return None
    growth = dict(ratio.points)
    deltas = []
    for cat, value in money.points:
        g = growth.get(cat)
        if g is None or g <= -0.999:
            continue
        deltas.append((cat, value - value / (1 + g)))
    if len(deltas) < 3:
        return None
    # A bridge reads left to right: every gain first (largest to smallest),
    # then the drags. Mixing a +2 between two negatives makes the staircase
    # look broken even when the arithmetic is right.
    deltas.sort(key=lambda kv: -kv[1])
    plus = sum(d for _, d in deltas if d > 0)
    minus = sum(d for _, d in deltas if d < 0)
    top = max(deltas, key=lambda kv: kv[1])[0]
    drag = min(deltas, key=lambda kv: kv[1])[0]
    if minus < 0 and abs(minus) > plus * 0.25:
        title = f"增量一半来自{top}，{drag}在往回拽"
    else:
        title = f"增量几乎全部来自{top}"
    return Slide(layout="chart", title=title, fact=f"{money.id},{ratio.id}",
                 variant="waterfall",
                 note="把增长拆开看：正贡献集中在少数区域，负贡献在拖后腿。",
                 bullets=[f"{top}独自贡献了大部分增量",
                          f"{drag}在往回拽，止血比拉新更急",
                          "两次下滑加起来，不到领涨区的两成"])


def _pull_quote(bundle) -> tuple[str, str]:
    """A sentence from the user's own documents, with no numbers in it.

    Numbers would have to be verifiable, and a quote that has been reworded to
    satisfy a checker is no longer a quote.
    """
    for d in getattr(bundle, "docs", []) or []:
        # Strip markdown scaffolding before splitting: a quote that starts
        # with "##" or ">" is a formatting artefact, not the author speaking.
        text = re.sub(r"^\s{0,3}#{1,6}\s*", "", d.text or "", flags=re.M)
        text = re.sub(r"^\s{0,3}[>\-*]\s*", "", text, flags=re.M)
        # A paragraph break ends a sentence. Erasing it glues a heading onto
        # the sentence that follows and ships "渠道复盘直营团队…" as a quote.
        text = re.sub(r"\n+", "。", text)
        text = re.sub(r"\s+", "", text)
        for chunk in re.split(r"(?<=[。！？])", text):
            line = chunk.strip("。！？ ")
            if 16 <= len(line) <= 58 and not re.search(r"\d", line):
                return line, Path(d.name).stem
    return "", ""


def _pct(v: float) -> str:
    return f"{v * 100:+.1f}%"


def _second(f: Fact) -> str:
    pts = _ranked(f)
    return pts[1][0] if len(pts) > 1 else ""


def _halves(f: Fact) -> tuple[float, float]:
    vals = [v for _, v in f.points]
    mid = len(vals) // 2
    first = sum(vals[:mid]) / max(mid, 1)
    second = sum(vals[mid:]) / max(len(vals) - mid, 1)
    return first, second


def _up_months(f: Fact) -> tuple[int, int]:
    vals = [v for _, v in f.points]
    diffs = [b - a for a, b in zip(vals, vals[1:])]
    return sum(1 for d in diffs if d > 0), sum(1 for d in diffs if d < 0)


def _join(names: list[str]) -> str:
    return "、".join(names[:2])


# --------------------------------------------------------------------------
# draft
# --------------------------------------------------------------------------


def _thin(bundle: Bundle) -> Plan:
    """No facts, no docs: say what is missing instead of staging an empty deck.

    A single column of numbers is not a story, and a three-page deck that says
    "经营复盘" while showing nothing is worse than a short honest page.
    """
    names = "、".join(Path(t.source).name for t in bundle.tables[:3]) or "这份材料"
    asks = [
        "再加一列可以比较的维度：时间（月/季）或者分类（区域、产品、渠道）",
        "补一列参照：同比、占比，或者目标值——没有参照就没有结论",
        "把想说明的那句话写进文档：一句话就够，模型会照它组织证据",
    ]
    return Plan(
        deck_title="这份材料还讲不出结论",
        subtitle="缺的不是设计，是可比较的数据",
        slides=[
            Slide("title", "这份材料还讲不出结论",
                  note=f"{names} 里只有孤立的数字，没有时间轴，也没有分类。"),
            Slide("bullets", "先补三样，再谈版式", bullets=asks,
                  note="补齐之后重新生成：页序、图表和标题都会跟着数据重排。"),
            Slide("closing", "补齐后再生成",
                  bullets=["一列时间或分类", "一列参照", "一句结论"]),
        ],
    )


def draft(bundle: Bundle, facts: list[Fact], *, deck_title: str = "",
          subtitle: str = "") -> Plan:
    used: set[str] = set()
    trend = _series(facts)
    ratio = _ratio(facts)
    money = _breakdowns(facts, used, 1)
    funnel = _funnel(facts)
    for f in [trend, ratio, *money, funnel]:
        if f:
            used.add(f.id)
    duration = _duration(facts, used)
    # Computed early because it decides whether the plain growth bar still
    # earns a page: a bridge and a ranking answer the same question, and the
    # bridge answers it better.
    water = _waterfall(facts)

    top_growth = _top(ratio) if ratio else ""
    neg = _neg(ratio) if ratio else []
    slow = _top(duration) if duration else ""
    funnel_first = funnel.points[0][0] if funnel else ""
    funnel_last = funnel.points[-1][0] if funnel else ""
    if not facts:
        return _thin(bundle)

    # --- cover: the thesis the rest of the deck has to earn -----------------
    parts: list[str] = []
    if top_growth:
        parts.append(f"{top_growth}领涨")
    if neg:
        parts.append(f"{_join(neg)}失血")
    elif ratio:
        parts.append(f"{_bottom(ratio)}垫底")
    if slow:
        parts.append(f"效率卡在{slow}")
    if not parts:
        # No trend to lead with: lead with the spread that is actually in the
        # data, instead of printing the same "经营复盘" on every dataset.
        bd = next((f for f in facts if f.kind == "breakdown" and len(f.points) > 1), None)
        if bd:
            ranked = sorted(bd.points, key=lambda kv: -abs(kv[1]))
            parts.append(f"{ranked[0][0]}{_metric(bd)}最高")
            parts.append(f"{ranked[-1][0]}最低")
    thesis = "，".join(parts) if parts else (deck_title or "经营复盘")

    sub_bits: list[str] = []
    if trend:
        first, second = _halves(trend)
        sub_bits.append(f"{_metric(trend)}全年{'走高' if second >= first else '走低'}")
    if ratio and len(ratio.points) > 3:
        vals = [v for _, v in ratio.points]
        # Name the metric, not a dimension we guessed: the same page shape has
        # to work for regions, channels and quarters.
        sub_bits.append(f"{ratio.column}从{min(vals):.0%}到{max(vals):.0%}")
    if funnel:
        sub_bits.append(f"从{funnel_first}到{funnel_last}层层收窄")
    sub = subtitle or ("，".join(sub_bits) if sub_bits else "从数据到动作")

    slides: list[Slide] = [
        Slide(layout="title", title=thesis, note=sub),
        Slide(layout="section", title="先看趋势，再看结构，最后看动作",
              note="结论在前，证据在后；每一页只回答一个问题。"),
    ]

    # --- trend --------------------------------------------------------------
    if trend:
        first, second = _halves(trend)
        up, down = _up_months(trend)
        rising = second >= first
        title = (f"{_metric(trend)}震荡上行，下半年明显加速" if rising
                 else f"{_metric(trend)}震荡走低，下半年仍在承压")
        bullets = [
            "全年没有连续两个月下滑" if up > down else "全年没有连续两个月回升",
            "下半年斜率高于上半年" if rising else "下半年仍未见修复",
            "每次回落都在次月收回" if up > down else "每次回升都很快被抹平",
        ]
        slides.append(Slide(layout="chart", title=title, fact=trend.id,
                            note="月度序列，首尾对比看趋势，单月波动不单独下结论。",
                            bullets=bullets))

    # --- the portfolio ------------------------------------------------------
    # One line answers "did it grow"; several lines answer "who grew", which is
    # the question the next budget meeting actually asks.
    multi = _multi(facts)
    if multi:
        moves = {}
        for cat, pts in multi.series.items():
            first, last = pts[0][1], pts[-1][1]
            moves[cat] = (last - first) / abs(first) if first else 0.0
        lead = max(moves, key=moves.get)
        lag = min(moves, key=moves.get)
        if moves[lead] > 0.02 and moves[lag] < moves[lead] - 0.05:
            title = f"{lead}在加速，{lag}没有跟上"
        elif moves[lead] > 0:
            title = f"{lead}撑住了盘子，其余按原速走"
        else:
            title = f"三条线一起走平，没有新的增长点"
        slides.append(Slide(
            layout="chart", title=title, fact=multi.id, variant="multi",
            note="三条线共用一条时间轴：看斜率差，不看单月高低。",
            bullets=[f"{lead}斜率最陡", f"{lag}几乎走平",
                     "差距在下半年拉开"]))

    # --- scale --------------------------------------------------------------
    orders = _scalar(facts, "订单")
    customers = _scalar(facts, "客户数", "客户")
    price = _scalar(facts, "客单价", "单价")
    if orders and customers:
        if orders.raw > customers.raw:
            ktitle = "订单数高于客户数，增长靠老客复购"
            knote = "客户数明显低于订单数，说明收入更多来自存量客户的重复购买。"
        else:
            ktitle = "客户数高于订单数，客单还有提升空间"
            knote = "客户数多于订单数，单客贡献偏低，提客单比拉新更直接。"
        picks = [f for f in (orders, price, customers) if f]
        slides.append(Slide(layout="kpi_row", title=ktitle,
                            fact=",".join(f.id for f in picks), note=knote))
    else:
        # No order/customer pair: a revenue-and-rate sheet still deserves a
        # scale page, otherwise the deck jumps from a trend line to a ranking.
        picks, seen_units = [], set()
        for want in ("money", "ratio", "count", "raw"):
            f = next((x for x in facts
                      if x.kind == "scalar" and x.unit == want and x.id not in {p.id for p in picks}),
                     None)
            if f:
                picks.append(f)
                seen_units.add(want)
        if len(picks) >= 2:
            slides.append(Slide(
                layout="kpi_row", title="先把这几个数对齐：规模与结构",
                fact=",".join(f.id for f in picks[:3]),
                note=f"{len(picks[:3])} 张卡片来自不同口径；先确认它们说的是同一段时间、同一批对象。"))

    # --- structure ----------------------------------------------------------
    if ratio and water is None:
        negs = _neg(ratio)
        if len(negs) == 1:
            title = f"{top_growth}领涨，{negs[0]}是唯一下滑"
        elif len(negs) >= 2:
            title = f"{top_growth}领涨，{_join(negs)}同时收缩"
        else:
            lo = dict(ratio.points)[_bottom(ratio)]
            title = (f"{top_growth}最高 {_val(ratio, top_growth) * 100:.1f}%，"
                     f"{_bottom(ratio)}最低 {lo * 100:.1f}%")
        # Name the metric, not "growth": the same page shape also carries
        # conversion rates and per-unit economics.
        metric = ratio.column or "增速"
        gap = (max(v for _, v in ratio.points) - min(v for _, v in ratio.points)) if ratio.points else 0
        bullets = [f"{top_growth}{metric}最高"]
        if negs:
            bullets.append(f"{_join(negs)}为负")
        else:
            bullets.append(f"{_bottom(ratio)}{metric}最低")
        bullets.append("首尾差距明显" if gap >= 0.05 else "首尾差距不大，别只看排名")
        slides.append(Slide(layout="chart", title=title, fact=ratio.id,
                            note="同比看方向，不看绝对规模；正负分界比排名更重要。",
                            bullets=bullets))

    # --- the bridge ---------------------------------------------------------
    # A growth bar chart says who is up; a bridge says how much each one moved
    # the total. Same two facts, a different question.
    if water:
        slides.append(water)

    # --- the mix ------------------------------------------------------------
    # Share is a different question from size: a donut makes "half the revenue
    # comes from one line" a shape you see before reading a number.
    # Prefer the product mix, but a channel or regional split of the same size
    # answers the same question — and a deck with no donut at all is flatter
    # than one that explains concentration wherever it finds it.
    TIME_DIM = re.compile(r"季|月|年|周|日|期|时间")

    def _mixable(f: Fact) -> bool:
        dim = f.label.split("按")[-1] if "按" in f.label else ""
        # A donut is a whole split into parts. Quarters and months are a
        # sequence, not parts of one thing: "一个季度吃掉 28%" is nonsense.
        return (f.points is not None and 3 <= len(f.points) <= 5
                and f.unit == "money" and f.chart == "bar"
                and not TIME_DIM.search(dim))

    mix = next((f for f in facts if _mixable(f)
                and ("产品" in f.label or "品类" in f.label)), None)
    if mix is None:
        mix = next((f for f in facts if _mixable(f) and f.id not in used), None)
    if mix:
        total = sum(abs(v) for _, v in mix.points) or 1
        top, top_v = max(mix.points, key=lambda kv: kv[1])
        bottom, bot_v = min(mix.points, key=lambda kv: kv[1])
        dim = mix.label.split("按")[-1].strip() if "按" in mix.label else ""
        unit_word = "产品线" if "产品" in mix.label else (dim or _metric(mix))
        slides.append(Slide(
            layout="chart", title=f"一个{unit_word}吃掉 {abs(top_v) / total:.0%} 的收入",
            fact=mix.id, variant="donut",
            note=f"{top} 占 {abs(top_v) / total:.0%}，{bottom} 只有 "
                 f"{abs(bot_v) / total:.0%}：结构比总量更值得看。",
            bullets=[f"{top}一项占 {abs(top_v) / total:.0%}",
                     f"{bottom}只占 {abs(bot_v) / total:.0%}",
                     f"{len(mix.points)} 项撑起全部{_metric(mix)}"]))

    # --- the split, side by side --------------------------------------------
    if ratio:
        ups = [(k, v) for k, v in _ranked(ratio) if v > 0][:2]
        downs = [(k, v) for k, v in _ranked(ratio) if v < 0][:2]
        if ups and downs:
            left = "、".join(f"{k} {_pct(v)}" for k, v in ups)
            right = "、".join(f"{k} {_pct(v)}" for k, v in downs)
            slides.append(Slide(
                layout="compare", title="一条线在涨，另一条在退",
                note="同一个市场，两种走势：预算该压向哪边，这页已经给出答案。",
                bullets=[f"还在涨：{left}", f"在退：{right}"]))

    # --- the scorecard ------------------------------------------------------
    # Regions ranked one measure at a time hide the interesting part: the
    # region that leads on size is rarely the one leading on growth.
    heat = [f for f in facts if f.points and len(f.points) >= 4
            and "区域" in f.label and f.unit in ("money", "ratio", "count")]
    heat = sorted(heat, key=lambda f: ("money" not in f.unit, f.id))[:3]
    base_cats = {k for k, _ in heat[0].points} if heat else set()
    heatmap_on = len(heat) >= 2 and all(
        {k for k, _ in f.points} == base_cats for f in heat)
    if heatmap_on:
        slides.append(Slide(
            layout="chart", title="规模、增速、客户，并不同步",
            fact=",".join(f.id for f in heat), variant="heatmap",
            note="每一列单独归一：颜色只比同一列里的高低，别跨列比深浅。",
            bullets=["规模第一不等于增速第一",
                     "负增长区域仍在贡献收入",
                     "客户数与规模并不同步"]))

    for f in money:
        if heatmap_on and "区域" in f.label:
            continue
        top, bottom = _top(f), _bottom(f)
        metric = _metric(f)
        if "区域" in f.label:
            title = f"{top}规模第一，{bottom}落在末位"
            note = f"头部与尾部之间是断层，不是渐变；{bottom}短期难以靠自然增长追上。"
        elif "产品" in f.label or "线" in f.label:
            title = f"{top}贡献最高，{bottom}占比最低"
            note = "结构决定毛利，也决定明年资源往哪边挪。"
        else:
            title = f"{metric}：{top}第一，{bottom}末位"
            share = abs(dict(f.points)[top]) / (sum(abs(v) for _, v in f.points) or 1)
            note = (f"{top} 占 {share:.0%}，{bottom} 只有 "
                    f"{abs(dict(f.points)[bottom]) / (sum(abs(v) for _, v in f.points) or 1):.0%}；"
                    f"平均值会掩盖这个差距。")
        # Scale is an argument, not a distribution: put the leader on the page
        # as one number so the deck changes pace after two chart pages.
        if "区域" in f.label and len(f.points) >= 3:
            share = abs(dict(f.points)[top]) / (sum(abs(v) for _, v in f.points) or 1)
            slides.append(Slide(
                layout="key_figure", fact=f.id,
                title=f"{top}一个区，顶三个区",
                note=f"规模不是均匀铺开的：{top}占 {share:.0%}，"
                     f"尾部 {bottom} 还在等一个能复制的打法。"))
        else:
            slides.append(Slide(layout="chart", title=title, fact=f.id, note=note,
                                bullets=[f"{top}排第一", f"{bottom}排最后",
                                         "头部集中，尾部偏低"]))

    # --- efficiency ---------------------------------------------------------
    b_conv: float | None = None
    if funnel:
        steps = funnel_steps(funnel.points)
        if steps:
            pairs = [(steps[i][0], steps[i + 1][0], steps[i + 1][2])
                     for i in range(len(steps) - 1)]
            worst = min(pairs, key=lambda t: t[2])
            b_from, b_to, b_conv = worst[0], worst[1], worst[2]
        else:
            diffs = [(funnel.points[i][0], funnel.points[i + 1][0],
                      funnel.points[i][1] - funnel.points[i + 1][1])
                     for i in range(len(funnel.points) - 1)]
            b_from, b_to, _ = max(diffs, key=lambda t: t[2])
        title = (f"{b_from}到{b_to}漏得最多，{funnel_last}只剩 "
                 f"{int(funnel_last_v)} 单") if (funnel_last_v := funnel.points[-1][1]) else \
                f"{b_from}到{b_to}的转化最弱"
        slides.append(Slide(layout="table", title=title, fact=funnel.id,
                            note=f"逐级转化看漏点：{b_from}到{b_to}这一跳最贵，"
                                 f"先修它，再谈拉新。"))

    if duration:
        title = f"{_top(duration)}停留最久，{_bottom(duration)}反而最短"
        slides.append(Slide(layout="chart", title=title, fact=duration.id,
                            note="停留时间就是现金周期；最慢的一环决定整条链的速度。",
                            bullets=[f"{_top(duration)}耗时最长",
                                     f"{_bottom(duration)}耗时最短",
                                     "慢在中间，不在两端"]))

    # --- source material ----------------------------------------------------
    # A deck made only of computed charts looks like a dashboard. One page of
    # the user's own material keeps the argument anchored to the real world,
    # and proves the tool read the pictures instead of only the tables.
    if bundle.pictures:
        # The photo already carries the cover; repeating it here would spend a
        # page saying nothing new. Prefer a document that the deck has not shown.
        charts = [p for p in bundle.pictures if p.kind in ("chart", "page", "screenshot")]
        photo = next((p for p in bundle.pictures if p.kind == "photo"), None)
        pic = charts[0] if charts else (photo or bundle.pictures[0])
        stem = Path(pic.name).stem
        if pic.kind == "photo":
            slides.append(Slide(
                layout="image_full", title=stem,
                image=pic.id,
                note="现场照片直接铺满整页：它提供的是语境，不是数据。"))
        else:
            notes = {
                "chart": "原始图表直接上页：细节和口径都留在原处，二次转述只会丢信息。",
                "page": "扫描页保留原始排版，方便当场对照原文。",
                "screenshot": "截图保留原始界面，避免转述造成误解。",
            }
            # A described picture can carry its own sentence; otherwise the
            # page states plainly why the original is on screen.
            note = (pic.note if pic.note and "×" not in pic.note
                    else notes.get(pic.kind, "用户提供的图片，原样上页，不做二次加工。"))
            slides.append(Slide(
                layout="split_image", title=f"原始材料：{stem}",
                image=pic.id, note=note))

    # --- pull quote ---------------------------------------------------------
    # One line in the client's own words changes the register of the deck: for
    # a moment the slides stop arguing and let the document speak.
    quote_text, quote_from = _pull_quote(bundle)
    if quote_text:
        slides.append(Slide(layout="quote", title=quote_text,
                            note=f"—— {quote_from}"))

    # --- action -------------------------------------------------------------
    acts: list[str] = []
    rich: list[str] = []
    if top_growth:
        second = _second(ratio) or top_growth
        acts.append(f"把资源压到{top_growth}，把打法复制到{second}")
        rich.append(
            f"{acts[-1]}：{top_growth} {_pct(_val(ratio, top_growth))}、"
            f"{second} {_pct(_val(ratio, second))}，增量集中在少数区域。"
        )
    if neg:
        acts.append(f"先止住{_join(neg)}的失血，再谈增长")
        ev = "、".join(f"{n} {_pct(_val(ratio, n))}" for n in neg[:2])
        rich.append(f"{acts[-1]}：{ev}，止血优先于拉新。")
    if funnel:
        acts.append(f"修{b_from}到{b_to}的转化，缩短成交周期")
        if b_conv is not None:
            rich.append(f"{acts[-1]}：{b_from}到{b_to}只有 {b_conv * 100:.1f}%，全链路最低。")
        else:
            rich.append(f"{acts[-1]}：这一跳流失最多，先修它。")
    if duration and len(acts) < 3:
        acts.append(f"压缩{slow}阶段的停留时间")
        rich.append(f"{acts[-1]}：{slow}平均 {_val(duration, slow):g} 天，全链路最久。")
    # A dataset without a funnel or a declining segment still owes the reader a
    # second move: fall back to structure (who carries the revenue) and to the
    # spread between best and worst, both of which are always computable.
    if len(acts) < 2 and ratio and len(ratio.points) >= 2:
        by_val = sorted(ratio.points, key=lambda kv: kv[1])
        lo, hi = by_val[0], by_val[-1]
        if lo[0] != top_growth:
            acts.append(f"把{lo[0]}补到{hi[0]}的水平")
            # Two facts, not a third one computed on the fly: the gap between
            # them is arithmetic the reader can do, and a difference we print
            # would need its own source.
            rich.append(f"{acts[-1]}：{lo[0]} {lo[1] * 100:.1f}%，"
                        f"{hi[0]} {hi[1] * 100:.1f}%。")
    if len(acts) < 2 and money:
        top_f = max(money, key=lambda f: abs(f.points[0][1]))
        if top_f.points[0][0] != top_growth:
            total = sum(abs(v) for _, v in top_f.points) or 1.0
            share = abs(top_f.points[0][1]) / total
            acts.append(f"把{top_f.points[0][0]}的打法复制出去，别只靠它一个")
            rich.append(f"{acts[-1]}：它占 {share:.0%} 的{top_f.column}，"
                        f"集中度太高，第二名只有 {abs(top_f.points[1][1]) / total:.0%}。")
    if len(acts) < 2 and money:
        # A portfolio answer, not a new claim: both numbers are shares of the
        # same column and appear on the chart page.
        f = money[0]
        ranked = sorted(f.points, key=lambda kv: -abs(kv[1]))
        if len(ranked) > 2:
            total = sum(abs(v) for _, v in ranked) or 1.0
            hi, lo = ranked[0], ranked[-1]
            acts.append(f"把资源从{lo[0]}挪一点给{hi[0]}")
            rich.append(f"{acts[-1]}：{hi[0]} 占 {abs(hi[1]) / total:.0%} 的{f.column}，"
                        f"{lo[0]} 只有 {abs(lo[1]) / total:.0%}，差距不是一点。")
    acts, rich = acts[:3], rich[:3]

    if rich:
        # The title has to count the same things the page shows. Promising three
        # moves and printing one is the kind of small lie a reader notices.
        n = len(rich)
        title = {1: "明年只做一件事", 2: "明年做两件事"}.get(n, "明年的动作，只留三件")
        slides.append(Slide(layout="bullets", title=title, note="", bullets=rich))
    # a closing title that names the three moves beats a generic "next steps"
    parts: list[str] = []
    if top_growth:
        parts.append(f"压{top_growth}")
    if neg:
        parts.append(f"止{neg[0]}")
    if funnel and b_to:
        parts.append(f"修{b_to}")
    closing_title = "三件事，按这个顺序" if len(parts) >= 2 else "接下来做什么"
    # The last page is a sign-off, not a second copy of the action page:
    # three words the room can repeat on the way out.
    signoff = parts[:3] if len(parts) >= 2 else acts
    slides.append(Slide(layout="closing", title=closing_title, note="",
                        bullets=signoff or ["把结论拆到区域", "把动作落到负责人",
                                            "把复盘固定到季度"]))

    # Rhythm pass: a bridge and a split answer the same ratio question and
    # would otherwise sit as the third chart in a row. Put the comparison
    # between them so the eye gets a different shape before the next chart.
    compare_i = next((i for i, sl in enumerate(slides) if sl.layout == "compare"), None)
    water_i = next((i for i, sl in enumerate(slides) if sl.variant == "waterfall"), None)
    if compare_i is not None and water_i is not None and compare_i > water_i + 1:
        item = slides.pop(compare_i)
        slides.insert(water_i + 1, item)

    # Rhythm guard: three identical layouts in a row turn a deck into a
    # spreadsheet. Move the next different page forward instead of leaving the
    # reader to blink at the same shape three times.
    for i in range(2, len(slides) - 1):
        if slides[i].layout == slides[i - 1].layout == slides[i - 2].layout:
            j = next((k for k in range(i + 1, len(slides) - 1)
                      if slides[k].layout != slides[i].layout), None)
            if j is not None:
                slides.insert(i, slides.pop(j))

    # A second chapter mark: one section page at the top tells the reader the
    # deck has a shape, a second one tells them where the argument turns from
    # evidence to choice. Without it the last third reads as more of the same.
    acts_i = next((i for i, sl in enumerate(slides) if sl.layout == "bullets"), None)
    if acts_i and acts_i >= 8:
        slides.insert(acts_i, Slide(layout="section", title="前面是证据，后面是取舍",
                                    note="数字讲完了；接下来只谈先做什么、不做什么。"))

    return Plan(deck_title=deck_title or thesis, subtitle=sub, slides=slides)

def _stem(name: str, seen: dict[str, int] | None = None) -> str:
    """Filename without extension and without the "-2" duplicate suffix."""
    return re.sub(r"-\d+$", "", Path(name).stem)


def _sentences(bundle, limit: int = 6) -> list[tuple[str, str]]:
    """Short, quotable sentences from the user's documents, in document order."""
    out: list[tuple[str, str]] = []
    for d in getattr(bundle, "docs", []) or []:
        text = re.sub(r"^\s{0,3}#{1,6}\s*", "", d.text or "", flags=re.M)
        text = re.sub(r"^\s{0,3}[>\-*]\s*", "", text, flags=re.M)
        # A paragraph break is a sentence boundary; a heading with no full stop
        # would otherwise be glued to the sentence under it.
        text = re.sub(r"\n+", "。", text)
        for chunk in re.split(r"(?<=[。！？])", text):
            line = re.sub(r"\s+", "", chunk).strip("。！？")
            if 12 <= len(line) <= 58 and not re.search(r"\d", line):
                if all(line != seen for seen, _ in out):
                    out.append((line, _stem(d.name)))
            if len(out) >= limit:
                return out
    return out


def draft_text(bundle) -> Plan:
    """A deck for documents that carry no numbers.

    No data means no charts and no KPIs — inventing either would be the one
    unforgivable thing this tool could do. What a memo can honestly give is a
    claim, three extracts and a close, so that is exactly what gets built.
    """
    docs = list(getattr(bundle, "docs", []) or [])
    name = _stem(docs[0].name) if docs else "这次汇报"
    picks = _sentences(bundle, 5)
    deck_title = f"{name}：先看结论" if picks else name
    slides: list[Slide] = [
        Slide("title", deck_title, note=f"来自 {len(docs)} 份文档，没有数据表，所以没有图表。"),
    ]
    if picks:
        slides.append(Slide("quote", picks[0][0], note=f"—— {picks[0][1]}"))
    rest = picks[1:4]
    if rest:
        cn = {1: "一", 2: "两", 3: "三"}.get(len(rest), str(len(rest)))
        sources = list(dict.fromkeys(src for _, src in rest))
        slides.append(Slide(
            "notes", f"文档里的{cn}句话",
            note="原文摘录，未改写 · 来源：" + "、".join(sources),
            bullets=[line for line, _ in rest],
        ))
    slides.append(Slide("closing", "把这几句话带进讨论", bullets=[]))
    return Plan(deck_title, "文档要点", slides)


def _pretty_pic(name: str) -> str:
    """`DSC_0412.jpg` is a filename, not a title. Say what the page shows."""
    stem = _stem(name)
    bare = re.sub(r"[-_ ]?\d+$", "", stem).strip()
    if re.fullmatch(r"(?i)hero|img|image|dsc|dscn|dji|photo|pic|screenshot|"
                    r"截图|照片|图片|现场", bare or ""):
        return "现场照片"
    return stem


def draft_pictures(bundle) -> Plan:
    """A deck for a folder of images with no numbers behind them.

    Photos carry context, not quantities. The honest deck is a cover, the
    pictures themselves at full size (documents get a plate so they stay
    readable), and one closing line — no chart, no KPI, no invented link
    between a factory floor and a revenue figure.
    """
    pics = [p for p in (getattr(bundle, "pictures", []) or [])][:4]
    if not pics:
        return Plan("这次汇报", "图片材料", [Slide("title", "这次汇报"),
                                             Slide("closing", "接下来做什么")])
    name = _pretty_pic(pics[0].name or "图片材料")
    title = f"{name}：先看现场" if len(pics) > 1 else name
    slides: list[Slide] = [
        Slide("title", title,
              note=f"{len(pics)} 张图片，没有数据表：这是一份看图说话的材料。"),
    ]
    # The cover already carries the first picture; repeating it on page two
    # reads as a mistake, so the body starts from the second one.
    for i, pic in enumerate(pics[1:], 2):
        kind = getattr(pic, "kind", "") or ""
        label = _pretty_pic(pic.name)
        if len(pics) > 1:
            # Digits in a title have to be backed by a fact; an ordinal is
            # not a number, so it is spelled the way a person would say it.
            cn = "一二三四五六七八九"
            # "第二张：区域分布" reads as an order; "区域分布 · 第二张" reads
            # like a filename with a suffix.
            label = f"第{cn[i - 1] if i <= 9 else i}张：{label}"
        if kind in ("photo", ""):
            slides.append(Slide("image_full", label, image=pic.id,
                                note="现场照片直接铺满整页：它提供的是语境，不是数据。"))
        else:
            slides.append(Slide("split_image", f"原始材料：{label}", image=pic.id,
                                note="原始文件直接上页，未重绘、未二次加工。"))
    slides.append(Slide("closing", "把这几张图带进讨论",
                        bullets=["先说清这是哪一段现场", "再说它改变了哪个判断"]))
    return Plan(title, "图片材料", slides)

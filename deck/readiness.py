"""What this pile of material can and cannot prove.

A deck generator that only says "here are your slides" hides the most useful
thing it knows: which arguments the data can carry. This module reads the
bundle and the extracted facts and returns a short, honest inventory — what we
have, what is missing, and the one addition that would buy the most.

It is deliberately rule-based. It runs before any model, so the answer is the
same offline, and it never invents a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .compute import Fact
from .ingest import Bundle


@dataclass
class Check:
    key: str
    label: str
    have: bool
    detail: str = ""


@dataclass
class Readiness:
    checks: list[Check] = field(default_factory=list)
    score: int = 0
    total: int = 0
    suggestion: str = ""

    @property
    def summary(self) -> str:
        return f"资料 {self.score}/{self.total}"

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "total": self.total,
            "summary": self.summary,
            "checks": [
                {"key": c.key, "label": c.label, "have": c.have, "detail": c.detail}
                for c in self.checks
            ],
            "suggestion": self.suggestion,
        }


def _has(facts: list[Fact], kind: str) -> bool:
    return any(f.kind == kind for f in facts)


def _labels(facts: list[Fact]) -> str:
    for f in facts:
        if f.unit == "ratio":
            return f.label
    return ""


def audit(bundle: Bundle, facts: list[Fact]) -> Readiness:
    """Six questions a reviewer would ask of the material, answered honestly."""
    checks: list[Check] = []

    series = [f for f in facts if len(f.points) >= 4 and f.chart == "line"]
    checks.append(Check(
        "trend", "时间趋势", bool(series),
        f"{series[0].label}" if series else "没有按月/按周的时间列",
    ))

    breakdown = [f for f in facts if len(f.points) >= 3 and f.chart in ("bar", "")]
    checks.append(Check(
        "breakdown", "结构拆分", bool(breakdown),
        f"{breakdown[0].label}" if breakdown else "没有区域 / 产品 / 渠道这类维度",
    ))

    ratio = [f for f in facts if f.unit == "ratio"]
    checks.append(Check(
        "ratio", "同比或占比", bool(ratio),
        _labels(ratio) if ratio else "没有增长率或占比，变化只能靠绝对值猜",
    ))

    funnel = [f for f in facts if f.chart == "funnel"]
    checks.append(Check(
        "funnel", "流程漏斗", bool(funnel),
        funnel[0].label if funnel else "没有阶段列，说不出卡在哪一环",
    ))

    pics = list(getattr(bundle, "pictures", []) or [])
    # A picture that was never classified still counts as a candidate photo:
    # the audit may run before the vision pass.
    photos = [
        p for p in pics
        if getattr(p, "kind", "") == "photo"
        or (not getattr(p, "kind", "") and str(getattr(p, "path", "")).lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
    ]
    checks.append(Check(
        "visual", "现场或产品图", bool(photos),
        f"{len(photos)} 张照片" if photos else (
            f"{len(pics)} 张图，但都是图表/截图" if pics else "没有图片，全篇只能靠字"
        ),
    ))

    docs = list(getattr(bundle, "docs", []) or [])
    checks.append(Check(
        "voice", "原始表述", bool(docs),
        f"{docs[0].name}" if docs else "没有文档，缺一句能直接引用的话",
    ))

    score = sum(1 for c in checks if c.have)
    rd = Readiness(checks=checks, score=score, total=len(checks))

    if not series:
        rd.suggestion = "补一张按月的流水（月份 + 金额），就能多出一页趋势和一句增速判断。"
    elif not ratio:
        rd.suggestion = "补一列同比或占比，趋势之外还能说清「谁在涨」。"
    elif not funnel:
        rd.suggestion = "如果有阶段字段（线索→商机→签约），转化卡点会比结构图更有用。"
    elif not photos:
        rd.suggestion = "补一张现场或产品照片，封面和材料页会立刻不像模板。"
    elif not docs:
        rd.suggestion = "补一份周报或复盘文档，可以挑一句原话做引语页。"
    else:
        rd.suggestion = "材料已经够支撑一版完整汇报；再多一份竞品或客户原话，论证会更硬。"
    return rd

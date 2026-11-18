"""Brief: work out what deck this is supposed to be, before writing any of it.

The failure this module exists to prevent is the one every AI deck generator
makes: it assumes the audience. Ask for "整理一下这些数据" and you get a
management summary, because that is what the system prompt says to produce.
Nobody declared that assumption, so nobody could correct it, and the deck is
wrong in a way the user only discovers on page four.

So the audience, the occasion, the decision being asked for, the things that
must not appear, and what "good" means all become explicit slots with a stated
confidence and a stated piece of evidence. Where the evidence is thin, the
program asks — a small number of closed questions, chosen by how much the
answer would change the deck, not by what would be nice to know.

Three rules keep this honest:

* Confidence is judged from the *shape of the evidence*, not from the model's
  self-report. A slot backed by a quote from a file scores 1.0; a slot the
  model says it "infers" scores 0.6; a slot with nothing behind it scores 0.3.
  Models are uniformly overconfident about their own guesses, so we do not ask.
* Questions come from a fixed bank. The model may only choose which ones to
  ask, never write new ones. A generated question is a generated assumption.
* A question that would not change the deck is not asked. Each bank entry
  carries the layouts it can move; if none of them are in play, it is dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .compute import Fact
from .ingest import Bundle
from .llm import Client, LLMError

# Six slots, not twelve. Every additional slot is another question the user has
# to answer, and the marginal slot buys less than the friction it costs.
SLOTS = ("audience", "occasion", "length", "decision", "avoid", "success")

LABEL = {
    "audience": "受众",
    "occasion": "场合",
    "length": "时长",
    "decision": "决策诉求",
    "avoid": "禁忌",
    "success": "成功标准",
}

# `decision` carries the most weight because it is the only slot that changes
# the *shape* of the argument. Audience changes the register, length changes
# the count of pages, but a deck asking for budget and a deck reporting status
# are different documents even at the same length for the same room.
WEIGHT = {"decision": 25, "audience": 20, "occasion": 15, "avoid": 15,
          "length": 15, "success": 10}

# Evidence shape -> score. Deliberately not model-reported confidence.
S_QUOTED = 1.0     # a string from a file, quoted back
S_STRONG = 0.6     # inferred from file names or structure, no quote
S_WEAK = 0.3       # plausible default
S_NONE = 0.0       # model declined


@dataclass
class Slot:
    key: str
    value: str = ""
    evidence: str = ""
    source: str = ""          # "quoted" | "inferred" | "default" | "none"
    assumed: bool = False     # filled by us, not by the user or a file

    @property
    def score(self) -> float:
        return {"quoted": S_QUOTED, "inferred": S_STRONG,
                "default": S_WEAK, "none": S_NONE}.get(self.source, S_NONE)

    @property
    def known(self) -> bool:
        return bool(self.value.strip()) and self.score >= S_STRONG


@dataclass
class Question:
    id: str
    slot: str
    ask: str
    options: list[str]
    why: str            # what changes depending on the answer
    default: str        # what we do if the user says nothing

    @property
    def cost(self) -> int:
        # A closed choice is one keystroke; an open one is a paragraph.
        return 1 if len(self.options) >= 2 else 3

    def impact(self, brief: "Brief") -> float:
        gap = 1.0 - brief.get(self.slot).score
        return WEIGHT.get(self.slot, 10) / 10.0 * gap

    def priority(self, brief: "Brief") -> float:
        return self.impact(brief) / self.cost


# The bank. Fixed text, fixed options: the model picks, it does not write.
BANK: tuple[Question, ...] = (
    Question(
        "Q_decision", "decision",
        "这份材料最后要谁点头、点什么头？",
        ["批预算/批资源", "认可结论、不用拍板", "汇报进度、对齐信息", "复盘问题、定整改"],
        "决定整份 deck 的骨架：要资源就得有方案页和收益测算，对齐信息只需要结论页。",
        "认可结论、不用拍板",
    ),
    Question(
        "Q_audience", "audience",
        "听众最关心的是增长、成本，还是风险？",
        ["增长与机会", "成本与效率", "风险与合规", "混着讲"],
        "决定哪个数字当封面主数字，以及每页标题往哪个方向下判断。",
        "增长与机会",
    ),
    Question(
        "Q_occasion", "occasion",
        "这是什么场合？",
        ["季度/年度经营会", "项目立项或评审", "向上汇报单点问题", "对外/对客户"],
        "决定页数和正式程度：经营会要 8 页起，单点问题 4 页就够。",
        "季度/年度经营会",
    ),
    Question(
        "Q_length", "length",
        "给你多少时间讲？",
        ["5 分钟（4–5 页）", "15 分钟（6–8 页）", "30 分钟以上（10 页以上）"],
        "决定页数上限，以及每页能放几个论点。",
        "15 分钟（6–8 页）",
    ),
    Question(
        "Q_avoid", "avoid",
        "有没有不能出现的东西？",
        ["客户名/合同金额要脱敏", "不要提竞品", "不要写未定的数", "没有限制"],
        "决定哪些字段要从表格里剔掉，以及校验时要不要拦。",
        "没有限制",
    ),
    Question(
        "Q_success", "success",
        "讲完之后，什么算这次讲成功了？",
        ["当场拍板", "拿到下一步的授权", "对方记住一个结论", "先把问题摆出来"],
        "决定结尾页写行动项还是写问题清单。",
        "对方记住一个结论",
    ),
    Question(
        "Q_conflict", "decision",
        "文档里写的目标和表里的数字对不上，以哪个为准？",
        ["以表里的数为准", "以文档里的目标为准", "两个都放，标清楚"],
        "决定事实表用哪一套口径；选错会让整份 deck 自相矛盾。",
        "以表里的数为准",
    ),
    Question(
        "Q_period", "occasion",
        "时间范围按哪个来？",
        ["按表里全部数据", "只看最近一个季度", "只看同比"],
        "决定取数窗口；混着取会让「增长」这个词在前后页指不同东西。",
        "按表里全部数据",
    ),
)

_INFER_SYSTEM = """你在帮一个人把工作文件变成一份汇报。你的任务不是写汇报，是判断这份汇报该是什么。

你要填六个槽位。每个槽位给出三样东西：
- value：一句话，具体到可以直接用，不要写「管理层」这种空话，要写「华东大区与总部的季度经营会」这种。
- evidence：如果文件里有直接依据，原样引用那一小段（不超过 20 字）。没有就留空字符串。
- source：只能是 "quoted"（文件里有原话）、"inferred"（从文件名/表结构推出来的）、"default"（你按常识填的）、"none"（你判断不出来）。

诚实比完整重要。判断不出来就填 "none" 并把 value 留空——留空会被追问，猜错会毁掉整份 deck。

只输出 JSON：
{"audience": {"value": "", "evidence": "", "source": "none"}, ...}
六个键必须是：audience, occasion, length, decision, avoid, success。"""


@dataclass
class Brief:
    slots: dict[str, Slot] = field(default_factory=dict)
    questions: list[Question] = field(default_factory=list)
    coverage: float = 1.0          # C: share of files we could read
    notes: list[str] = field(default_factory=list)

    def get(self, key: str) -> Slot:
        return self.slots.get(key) or Slot(key)

    @property
    def confidence(self) -> float:
        earned = sum(WEIGHT[k] * self.get(k).score for k in SLOTS)
        return round(self.coverage * earned / 100.0, 3)

    @property
    def mode(self) -> str:
        r = self.confidence
        if r >= 0.75:
            return "auto"
        if r >= 0.50:
            return "ask_min"
        if r >= 0.30:
            return "ask_full"
        return "blocked"

    def ask_count(self) -> int:
        return {"auto": 0, "ask_min": 2, "ask_full": 4, "blocked": 1}[self.mode]

    def assumptions(self) -> list[str]:
        return [f"{LABEL[k]}：{self.get(k).value}"
                for k in SLOTS
                if self.get(k).assumed and self.get(k).value]

    def block(self) -> str:
        """The text the planner reads instead of a bare user sentence."""
        out = ["【这份 deck 是什么】"]
        for k in SLOTS:
            s = self.get(k)
            if not s.value:
                continue
            tail = "（假设）" if s.assumed else (f"（依据：{s.evidence}）" if s.evidence else "")
            out.append(f"- {LABEL[k]}：{s.value}{tail}")
        if self.notes:
            out.append("【文件里发现的情况】")
            out.extend(f"- {n}" for n in self.notes)
        return "\n".join(out)

    def to_json(self) -> str:
        return json.dumps({
            "confidence": self.confidence,
            "mode": self.mode,
            "coverage": self.coverage,
            "slots": {k: vars(self.get(k)) for k in SLOTS},
            "questions": [vars(q) for q in self.questions],
            "notes": self.notes,
        }, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "Brief":
        d = json.loads(raw)
        b = cls(coverage=d.get("coverage", 1.0), notes=d.get("notes", []))
        for k in SLOTS:
            b.slots[k] = Slot(**d["slots"][k])
        b.questions = [Question(**q) for q in d.get("questions", [])]
        return b


# --- stage 1: scan, no model ---------------------------------------------

def scan(bundle: Bundle) -> tuple[float, list[str]]:
    """What the files themselves say, before any model runs.

    Returns (coverage, notes). Coverage is the share of files we could actually
    read: a deck built on 40% of the input is a deck built on a guess, and the
    confidence score has to know that.
    """
    total = len(bundle.tables) + len(bundle.docs) + len(bundle.pictures) + len(bundle.skipped)
    read = len(bundle.tables) + len(bundle.docs) + len(bundle.pictures)
    coverage = (read / total) if total else 0.0

    notes: list[str] = []
    if bundle.skipped:
        names = ", ".join(Path(s).name for s in bundle.skipped[:4])
        notes.append(f"{len(bundle.skipped)} 个文件读不了：{names}")
    if len(bundle.tables) > 1:
        notes.append(f"有 {len(bundle.tables)} 张表，需要判断哪张是主表")
    if bundle.docs:
        notes.append(f"有 {len(bundle.docs)} 份文档，文字材料里的口径可能和表不一致")
    return round(coverage, 3), notes


# --- stage 2: infer, one model call --------------------------------------

def _inventory(bundle: Bundle, facts: list[Fact], limit: int = 24) -> str:
    lines = ["【文件清单】"]
    for t in bundle.tables[:8]:
        cols = ", ".join(str(c) for c in list(t.df.columns)[:10])
        lines.append(f"- 表 {t.name}（{len(t.df)} 行）：{cols}")
    for d in bundle.docs[:4]:
        lines.append(f"- 文档 {d.name}：{d.text[:160].replace(chr(10), ' ')}")
    for p in bundle.pictures[:4]:
        lines.append(f"- 图片 {p.name}（{p.width}×{p.height}）")
    lines.append("【算出来的关键数字】")
    for f in facts[:limit]:
        lines.append(f"- {f.id} {f.label} = {f.value}")
    return "\n".join(lines)


def infer(bundle: Bundle, facts: list[Fact], ask: str, *,
          client: Client) -> dict[str, Slot]:
    user = f"{_inventory(bundle, facts)}\n\n用户原话：{ask or '（没说）'}"
    try:
        raw = client.json(_INFER_SYSTEM, user, temperature=0.0, max_tokens=1200)
    except (LLMError, ValueError):
        # A failed inference is not a failed build: every slot falls back to
        # "unknown", confidence collapses, and the program asks instead.
        return {k: Slot(k) for k in SLOTS}

    slots: dict[str, Slot] = {}
    for k in SLOTS:
        d = raw.get(k) or {}
        src = str(d.get("source") or "none")
        if src not in ("quoted", "inferred", "default", "none"):
            src = "none"
        ev = str(d.get("evidence") or "").strip()
        # The model may claim "quoted" without quoting anything. Evidence is
        # the thing we can check, so it decides.
        if src == "quoted" and not ev:
            src = "inferred"
        slots[k] = Slot(k, str(d.get("value") or "").strip(), ev, src)
    return slots


# --- stage 3: score + choose questions, no model -------------------------

def _candidates(bundle: Bundle, brief: Brief) -> list[Question]:
    """Drop questions the files already answer, and ones nothing depends on."""
    out = []
    for q in BANK:
        if brief.get(q.slot).score >= S_QUOTED:
            continue          # the files said it outright; asking is noise
        if q.id == "Q_conflict" and not (bundle.tables and bundle.docs):
            continue          # no documents, so no possible conflict
        if q.id == "Q_period" and not bundle.tables:
            continue
        out.append(q)
    return out


def choose(bundle: Bundle, brief: Brief) -> list[Question]:
    n = brief.ask_count()
    if n == 0:
        return []
    ranked = sorted(_candidates(bundle, brief), key=lambda q: -q.priority(brief))
    return ranked[:n]


def apply_answers(brief: Brief, answers: dict[str, str]) -> None:
    for q in brief.questions:
        got = answers.get(q.id) or answers.get(q.slot)
        if not got:
            continue
        s = brief.get(q.slot)
        s.value, s.evidence, s.source, s.assumed = got, "用户回答", "quoted", False


def fill_defaults(brief: Brief) -> None:
    """Everything still unknown gets its question's default, marked assumed.

    A deck built on a stated assumption can be corrected in one sentence. A
    deck built on an unstated one has to be rebuilt.
    """
    for q in brief.questions:
        s = brief.get(q.slot)
        if not s.value:
            s.value, s.source, s.assumed = q.default, "default", True


# --- public --------------------------------------------------------------

def make(bundle: Bundle, facts: list[Fact], ask: str, *,
         client: Client | None = None,
         answers: dict[str, str] | None = None) -> Brief:
    coverage, notes = scan(bundle)
    brief = Brief(coverage=coverage, notes=notes)

    if client is not None:
        brief.slots = infer(bundle, facts, ask, client=client)
    else:
        brief.slots = {k: Slot(k) for k in SLOTS}

    brief.questions = choose(bundle, brief)
    if answers:
        apply_answers(brief, answers)
        # Answering may have raised confidence enough to need fewer questions.
        brief.questions = choose(bundle, brief)
    fill_defaults(brief)
    return brief

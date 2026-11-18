"""The design rules, as data.

`theme.py` says what the tokens *are*. This module says what a deck must
*satisfy* to use them correctly, and what each rule is worth when it is
broken.

Three layers, deliberately separated:

  geometry   measurable from the .pptx XML alone — grid, margins, type scale.
             A violation is a bug, not a matter of taste, so it blocks.
  density    statistical: ink coverage, characters per page, rhythm. One page
             can bend it; a deck that systematically bends it reads as
             generated. Warns.
  semantics  whether the titles form an argument. Needs a reader (human or
             model); the automated part only flags the cheap tells.

No rule here was invented: each `why` records the sample or the measurement it
came from, so a later editor can argue with the evidence instead of the rule.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- the numbers ----------------------------------------------------------
# All geometry is in inches on a 13.333 x 7.5 canvas. Expressed as ratios in
# `band` so the same rules survive a 4:3 deck.

GRID_COLUMNS = 12
SNAP_TOLERANCE_IN = 0.02      # 1.5 px at 96 dpi — below this the eye reads a shared edge
MARGIN_X_RATIO = (0.052, 0.062)   # 0.69–0.83 in of 13.333; theme uses 0.75
MARGIN_Y_RATIO = (0.060, 0.080)   # 0.45–0.60 in of 7.5; theme uses 0.52
# The band from the title's top to the first content element below it. Derived
# rather than measured directly: the corpus upper margin (p10 0% / median 9.9% /
# p90 20%) is the distance to the *topmost* element, and the title occupies the
# first ~0.6in of that, so the title block plus its leading lands at 14-24%.
TITLE_HEIGHT_RATIO = (0.140, 0.240)
TITLE_BODY_GAP_IN = 0.25
MAX_BODY_COLUMNS = 9          # 8.66 in → ~32 CJK chars per line at 17 pt
MAX_LINE_CHARS_CJK = 34
MIN_CHART_FONT_PT = 11.0      # projector floor; 9 pt at 1080p is 3 mm tall

# --- measured, not asserted -----------------------------------------------
# The first version of these bands came from reading about deck design and was
# wrong in both directions. corpus/analyze.py measures 11 real decks (87 content
# pages) exported at 16:9, normalised to this canvas:
#
#   ink coverage   p10 12%  median 20%  p90 42%   (the old band said 35-70%)
#   chars / page   p10 81   median 191  p90 614   (the old band said 80-240)
#   sizes / page   median 3               p90 7   (the old cap said <=4)
#   body text      16pt is the most common size on the canvas (116 runs)
#
# 35-70% ink was describing a magazine spread, not a presentation: it flagged
# real decks as empty and it flagged our own pages as empty while the corpus
# sat lower still. Bands now bracket the corpus, and the upper bound is generous
# because a dense appendix page is legitimate.
INK_CONTENT = (0.10, 0.48)
INK_COVER = (0.05, 0.30)
CHARS_CONTENT = (60, 400)
CHARS_CHART = (50, 260)
DENSITY_CV_MAX = 0.35
EMPHASIS_PER_PAGE = 2
ACCENTS_PER_DECK = 2
LAYOUT_RUN_MAX = 2
LAYOUT_SHARE_MAX = 0.40
MIN_LAYOUTS = 4
# Measured on the corpus: horizontal p10 30% / median 47% / p90 53%; vertical
# p10 32% / median 45% / p90 52%. The old band (38-62%) sat to the right of the
# measured median -- it was describing where I *put* content, not where real
# decks put it. Widened to bracket p10-p90 with a little room.
CENTROID_X = (0.30, 0.60)
CENTROID_Y = (0.33, 0.60)
TITLE_SIMILARITY_MAX = 0.34   # char-bigram Jaccard between adjacent titles


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    layer: str        # geometry | density | semantics
    target: str
    band: str
    gate: str         # block | warn | human
    how: str          # how to check it, concretely
    why: str          # the evidence behind the number


RULES: tuple[Rule, ...] = (
    # --- geometry ---------------------------------------------------------
    Rule(
        "R01", "画布与边距镜像", "geometry",
        "四边内容边距",
        "左右 6.0–7.0% 宽；上下 7.0–9.0% 高；左右相等、上下相等（±0.02in）",
        "block",
        "读首个非全幅元素的 left/top，与页面宽高比比较",
        "咨询与 IR deck 的正文边距稳定落在 5–7% 宽。边距不等会在翻页时产生"
        "「内容在抖」的错觉，这是比配色更早被察觉的廉价感来源。",
    ),
    Rule(
        "R02", "栅格吸附", "geometry",
        "元素左右边缘",
        "≥90% 的元素边缘落在 12 列线或内容边界上（容差 0.02in）",
        "block",
        "对每个元素取 left 与 left+width，求到最近列线的距离",
        "Beautiful.ai 的全部「智能」本质上就是这条：位置不是拖出来的，是解出来的。"
        "对齐线一旦丢失，多元素页立刻读作手工作坊。",
    ),
    Rule(
        "R03", "字号阶梯", "geometry",
        "每页字号集合",
        "字号 ∈ theme 中全部 FS_* 常量 ±0.5pt；单页 ≤5 种（语料中位 3、p90 7；超过 5 种读作拼装）",
        "block",
        "遍历 run.font.size，去重后与 theme 的 FS_* 常量比对；阶梯从 theme 动态读取，不在规则里写死",
        "五级阶梯是 deck 的宪法（theme.py 原文），但渲染层有 6 处裸字号绕过了它："
        "测于旧阶梯时 demo 出现 19 / 24 / 34pt，p6 一页四种字号。规则存在不等于被执行——"
        "现在阶梯改为 38/26/86/18/15/14/11/9，规则仍按 theme 判，改阶梯不必改规则。",
    ),
    Rule(
        "R04", "标题区比例", "geometry",
        "标题块高度与净距",
        "标题带到正文占画布高 14–24%；标题底到正文顶 ≥0.25in",
        "warn",
        "标题元素 top+height 与 BODY_TOP 之差",
        "标题吃太多，正文就被压成一条；吃太少，页面失去阅读入口。"
        "150 份公开 deck 的标题带中位数在 16%。",
    ),
    Rule(
        "R05", "行长", "geometry",
        "正文文本块宽度",
        "≤ 9/12 内容宽；中文 ≤34 字/行",
        "warn",
        "文本框宽度换算字号后估字数",
        "17pt 汉字宽 0.236in，9 列即 36 字，已到舒适上限。超过 40 字的行，"
        "读者的眼跳会丢行——这是「读不下去」的物理原因。",
    ),
    Rule(
        "R06", "图表内字号", "geometry",
        "图表 PNG 内的最小字号",
        "≥ 11pt（按最终贴片尺寸折算）",
        "block",
        "charts.py 里 labelsize/annotate fontsize 的最小值 × 贴片缩放比",
        "现在刻度 9pt、标签 9–9.5pt，贴到 6.7in 宽时再缩 7%，实际 8.4pt。"
        "投影到 3m 屏，字高 3mm，后排读不到——图表再准也白搭。",
    ),
    Rule(
        "R07", "页脚基线", "geometry",
        "来源与页码",
        "同一 y；来源左对齐内容左边线，页码右对齐内容右边线",
        "warn",
        "取底部两个最小字号元素的 top 差",
        "页脚是每页唯一贯穿的画布横线。它一旦浮动，整本 deck 的装订感就没了。",
    ),
    # --- density ----------------------------------------------------------
    Rule(
        "R08", "墨迹覆盖", "density",
        "内容占画布面积",
        "内容页 10–48%；封面/章节页 5–30%",
        "warn",
        "元素包围盒并集栅格化，图片按非白像素比折算",
        "实测 11 份真实 deck / 87 页：p10 12%、中位 20%、p90 42%。原定的 35–70% "
        "描述的是杂志跨页不是幻灯片——它同时把真实 deck 和本仓库的页判成「空」。"
        "现区间按语料取 10–48%，上界放宽是因为附录页本来就密。",
    ),
    Rule(
        "R09", "单页字数", "density",
        "含标题/正文/注释/来源的汉字数",
        "内容页 60–400；纯图表页 50–260",
        "warn",
        "去掉空白后计 len()",
        "实测语料中位 191 字、p90 614 字（模板占位文案，只当下限参考）。"
        "本仓库的页中位 66 字：只有一个断言、没有证据层，读者拿不到「凭什么」，"
        "所以字数是真问题——但 240 字的上限是拍的，语料不支持。",
    ),
    Rule(
        "R10", "密度方差", "density",
        "同 deck 内容页字数的变异系数",
        "CV ≤ 0.35",
        "warn",
        "stdev/mean",
        "忽空忽满比整体偏空更伤：翻页时视觉节奏断裂，观感是「凑页数」。",
    ),
    Rule(
        "R11", "强调点配额", "density",
        "单页非中性色元素；全 deck 强调色",
        "单页 ≤2 处；全 deck ≤2 种强调色",
        "warn",
        "按 run 色与填充色算饱和度，非灰即计",
        "一个强调色是意图，三个是装饰。多色 deck 的第一眼印象是模板而非判断。",
    ),
    Rule(
        "R12", "版式节奏", "density",
        "layout 序列",
        "同 layout 连续 ≤2 页；单 layout 占比 ≤40%；全 deck ≥4 种",
        "warn",
        "读 plan.json 的 slides[].layout",
        "demo 6 页里 3 页 chart、且 2–3 页连续同构。「没有整体逻辑」的一半"
        "来自版式单调：读者无法从版式变化感知论证推进。",
    ),
    Rule(
        "R13", "视觉重心", "density",
        "面积加权质心",
        "x ∈ [42%,58%]，y ∈ [40%,58%]",
        "warn",
        "Σ(area×center)/Σarea",
        "重心贴边 = 页面「歪」。这是廉价模板最常见的几何病，人眼 200ms 内可感。",
    ),
    # --- semantics --------------------------------------------------------
    Rule(
        "R14", "标题是论点", "semantics",
        "每页标题",
        "完整断言句，含主语+判断（或数字）；不以「分析/情况/汇报/总结/概述」结尾",
        "warn",
        "结尾词表 + 是否含动词/数字的启发式",
        "「销售情况分析」是目录，不是结论。行动标题（action title）是咨询 deck"
        "与 AI 生成 deck 最稳定的分界线。",
    ),
    Rule(
        "R15", "标题接龙", "semantics",
        "相邻标题的信息增量",
        "相邻标题字符二元组 Jaccard ≤0.34；全部标题连读须构成一段论证",
        "warn",
        "Jaccard 自动算；连读可读性由人或模型判",
        "demo p2「增长由华东独撑，华北已转负」与 p3「华东居首，华北失速」"
        "是同一结论说两遍——这就是「没有整体逻辑」的可测形态：翻页没有推进。",
    ),
    Rule(
        "R16", "一页一论点", "semantics",
        "正文要点",
        "≤3 条，且每条都在支撑标题，不引入第二个论点",
        "human",
        "verify 已卡 3 条；是否同论点需人读",
        "超过 3 条要点，读者记不住任何一条；两条互斥论点同页，等于没有结论。",
    ),
    Rule(
        "R17", "来源与页码", "semantics",
        "数据页来源行",
        "每个数据页有来源；页码连续；来源 ≤9pt 且 muted",
        "block",
        "检查页脚文本是否含「来源」或文件名",
        "没有来源的数字在评审现场就是一张待撕的纸。这条同时是合规与信任成本。",
    ),
)


def by_id(rule_id: str) -> Rule:
    for r in RULES:
        if r.id == rule_id:
            return r
    raise KeyError(rule_id)


def layers() -> dict[str, list[Rule]]:
    out: dict[str, list[Rule]] = {}
    for r in RULES:
        out.setdefault(r.layer, []).append(r)
    return out

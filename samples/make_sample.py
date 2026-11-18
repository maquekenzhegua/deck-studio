"""Generate a realistic sample folder so the pipeline can be tested without
asking anyone for their real work files.

Deliberately messy in the ways real work is messy: three sheets, a wide sheet,
a long-form sheet, a Word summary, and two images the boss sent at 11pm.
"""

from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent / "data"

PRODUCTS = ["智能硬件", "企业软件", "运维服务"]
REGIONS = ["华东", "华南", "华北", "西南", "东北", "西北"]


def monthly() -> pd.DataFrame:
    rng = random.Random(7)
    rows = []
    for m in range(1, 13):
        for p in PRODUCTS:
            base = {"智能硬件": 820_000, "企业软件": 540_000, "运维服务": 260_000}[p]
            season = 1 + 0.22 * (m >= 9) + 0.06 * (m in (3, 6))
            trend = 1 + 0.028 * m
            noise = rng.uniform(0.9, 1.1)
            amount = base * season * trend * noise
            rows.append(
                {
                    "月份": f"2024-{m:02d}",
                    "产品线": p,
                    "销售额": round(amount, 2),
                    "订单数": int(amount / rng.uniform(4200, 6800)),
                    "客单价": round(amount / max(1, int(amount / 5000)), 2),
                }
            )
    return pd.DataFrame(rows)


def regional() -> pd.DataFrame:
    rng = random.Random(11)
    rows = []
    for r in REGIONS:
        base = {"华东": 9_800_000, "华南": 6_400_000, "华北": 5_100_000,
                "西南": 2_300_000, "东北": 1_600_000, "西北": 900_000}[r]
        yoy = {"华东": 0.34, "华南": 0.11, "华北": -0.04,
               "西南": 0.19, "东北": -0.12, "西北": 0.02}[r]
        rows.append({
            "区域": r,
            "销售额": round(base * rng.uniform(0.97, 1.03), 2),
            "同比增长": yoy,
            "客户数": rng.randint(60, 420),
        })
    return pd.DataFrame(rows)


def pipeline() -> pd.DataFrame:
    rng = random.Random(3)
    stages = ["线索", "商机", "方案", "签约"]
    counts = [4200, 1860, 720, 318]
    return pd.DataFrame({
        "阶段": stages,
        "数量": counts,
        "平均停留天数": [3.2, 9.4, 14.1, 6.8],
        "转化率": [1.0, round(1860 / 4200, 3), round(720 / 1860, 3), round(318 / 720, 3)],
    })


def make_docx() -> None:
    import docx

    d = docx.Document()
    d.add_heading("2024 年度经营小结", level=1)
    d.add_paragraph(
        "全年收入结构继续向服务倾斜，企业软件与运维服务的合计占比由年初的 41% 提升至年末的 52%。"
        "智能硬件仍是最大收入来源，但增速在下半年明显放缓，主要原因是渠道库存调整。"
    )
    d.add_heading("主要问题", level=2)
    d.add_paragraph("华北与东北两个区域连续两个季度负增长，其中东北的客户流失集中在中小客户段。")
    d.add_paragraph("线索到商机的转化率维持在 44% 左右，低于行业常见的 55%，方案阶段的平均停留天数偏长。")
    d.add_heading("下一年度的三件事", level=2)
    d.add_paragraph("一、把华东的增长模型复制到西南；二、东北聚焦留存而非新签；三、缩短方案阶段周期。")
    d.save(ROOT / "经营小结.docx")


def make_images() -> None:
    """Two figures that look like something a BI tool would export.

    These stand in for the screenshots people actually paste into decks. They
    are real charts rather than grey rectangles because the point of the demo
    is what the renderer does with a real figure on a slide.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["savefig.dpi"] = 200

    # Match theme.py exactly. A pasted screenshot in a different blue is
    # the fastest way to make a deck look assembled from parts.
    accent, mid, pale = "#16325C", "#7D95B8", "#C3CAD4"
    ink, muted, hair = "#11141A", "#838B98", "#D8DDE5"

    # --- 1. regional sales, horizontal bars -------------------------------
    reg = regional().sort_values("销售额", ascending=True)
    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    fig.patch.set_facecolor("white")
    ys = range(len(reg))
    ax.barh(list(ys), reg["销售额"] / 1e4, color=pale, height=0.6, zorder=3)
    ax.barh([len(reg) - 1], [reg["销售额"].iloc[-1] / 1e4], color=accent, height=0.6, zorder=3)
    for y, v in zip(ys, reg["销售额"] / 1e4):
        ax.annotate(f"{v:,.0f}", (v, y), xytext=(7, 0), textcoords="offset points",
                    va="center", fontsize=10, color=ink)
    ax.set_yticks(list(ys))
    ax.set_yticklabels(list(reg["区域"]), fontsize=11, color=ink)
    ax.set_xticks([])
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    ax.set_title("各区域销售额（万元）", loc="left", fontsize=14, color=ink,
                 fontweight="bold", pad=16)
    ax.margins(x=0.14, y=0.04)
    fig.tight_layout(pad=0.6)
    fig.savefig(ROOT / "区域分布示意.png", facecolor="white")
    plt.close(fig)

    # --- 2. monthly revenue mix, stacked columns --------------------------
    m = monthly()
    wide = (m.pivot_table(index="月份", columns="产品线", values="销售额", aggfunc="sum")
             .reindex(columns=["智能硬件", "企业软件", "运维服务"]).fillna(0.0))
    fig, ax = plt.subplots(figsize=(6.6, 4.6))
    fig.patch.set_facecolor("white")
    bottom = [0.0] * len(wide)
    for line, color in zip(["智能硬件", "企业软件", "运维服务"], [accent, mid, pale]):
        vals = (wide[line] / 1e4).tolist()
        ax.bar(range(len(wide)), vals, bottom=bottom, color=color, width=0.66,
               label=line, zorder=3)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_xticks(range(0, len(wide), 2))
    ax.set_xticklabels([f"{i + 1}月" for i in range(0, len(wide), 2)], fontsize=10, color=muted)
    ax.set_yticks([])
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(loc="upper left", frameon=False, fontsize=10, ncol=3,
              bbox_to_anchor=(0, 1.02), handlelength=1.1)
    ax.set_title("月度销售额结构（万元）", loc="left", fontsize=14, color=ink,
                 fontweight="bold", pad=26)
    ax.margins(y=0.10)
    fig.tight_layout(pad=0.6)
    fig.savefig(ROOT / "产品线结构.png", facecolor="white")
    plt.close(fig)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(ROOT / "销售数据.xlsx", engine="openpyxl") as w:
        monthly().to_excel(w, sheet_name="月度销售", index=False)
        regional().to_excel(w, sheet_name="区域", index=False)
        pipeline().to_excel(w, sheet_name="漏斗", index=False)
    make_docx()
    make_images()
    print(f"样例已生成：{ROOT}")


if __name__ == "__main__":
    main()

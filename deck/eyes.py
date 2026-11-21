"""Look at the pages we just made, and say what a designer would say.

Everything else in this project checks the deck *structurally*: the numbers are
verifiable, the boxes do not overlap, the text fits. None of that notices a page
that is technically valid and visually dead. A vision model can — it is the only
reader in the pipeline that sees the composition instead of the data.

Two rules keep this honest:

* it may only complain about layout, never about content (content is verified
  against facts; a model's opinion there is noise);
* every complaint must arrive with a fix someone can execute. "Looks crowded" is
  not a finding.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import llm

LOOK_SYSTEM = """你是一份商业演示的版式总监。你会看到若干张已经排好的页面，按顺序编号。
只评价版式与视觉层次：留白、对齐、字号对比、视线落点、图表可读性、页面是否失衡。
不要评价内容是否正确，不要改写文案，不要提出加页或删页。

对每一页，只指出最明显的一个问题，并给一句可以照着做的修法（例如「右侧读法栏与图表基线不对齐，把三条读法整体下移 0.3 英寸」）。

只输出 JSON：
{"notes":[{"page":1,"problem":"…","fix":"…"}]}
没有问题的页面不要出现在结果里。"""

MAX_PAGES = 8
# A sixteen-page deck should not be reviewed half-way: walk it in batches of
# MAX_PAGES instead of silently looking at the first eight.
MAX_CHUNKS = 2
MAX_TOTAL = MAX_PAGES * MAX_CHUNKS


@dataclass
class Note:
    page: int
    problem: str
    fix: str


def _small(src: Path, out_dir: Path, width: int = 960) -> Path:
    """Shrink a render before it goes over the wire."""
    from PIL import Image

    im = Image.open(src).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    dst = out_dir / (Path(src).stem + ".jpg")
    im.save(dst, "JPEG", quality=82)
    return dst


def _notes(raw: str, limit: int, offset: int = 0) -> list[Note]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    out: list[Note] = []
    for item in (data.get("notes") or [])[:limit]:
        try:
            page = int(item.get("page") or 0)
        except (TypeError, ValueError):
            continue
        problem = str(item.get("problem") or "").strip()[:90]
        fix = str(item.get("fix") or "").strip()[:120]
        if 1 <= page <= max(1, limit) and problem and fix:
            out.append(Note(page + offset, problem, fix))
    return out


def look(pages: list[Path], client: llm.Client, *, limit: int = MAX_PAGES) -> list[Note]:
    """Walk the deck in batches of `limit` pages; return notes in page order."""
    picked = [Path(p) for p in pages if Path(p).exists()][:MAX_TOTAL]
    if not picked:
        raise ValueError("还没有可以看的页面")
    found: list[Note] = []
    with tempfile.TemporaryDirectory(prefix="deck-eyes-") as tmp:
        for start in range(0, len(picked), limit):
            batch = picked[start:start + limit]
            small = [_small(p, Path(tmp)) for p in batch]
            listing = "、".join(f"第 {start + i} 页" for i in range(1, len(small) + 1))
            text = (f"这是同一份演示里的 {len(small)} 张页面，依次为 {listing}。"
                    "请按页指出最明显的一处版式问题。")
            raw = client.vision(LOOK_SYSTEM, text, small, max_tokens=900)
            found.extend(_notes(raw, len(small), start))
    seen = set()
    out: list[Note] = []
    for n in sorted(found, key=lambda x: x.page):
        if n.page in seen:
            continue
        seen.add(n.page)
        out.append(n)
    return out


def as_prompt(notes: list[Note]) -> str:
    lines = [f"第 {n.page} 页：{n.problem}。改成：{n.fix}。" for n in notes[:6]]
    return "按版式总监的意见调整页面（只改版式与页面上的措辞，不要动数字，不要增删页）：\n" + "\n".join(lines)


def summary(notes: list[Note], looked: int = 0) -> str:
    if not notes:
        # Saying how many pages were actually read keeps "no notes" from
        # reading like the model never opened the file.
        return (f"看了 {looked} 页，没有发现明显的版式问题" if looked
                else "模型看过了，没有发现明显的版式问题")
    pages = "、".join(str(n.page) for n in notes)
    head = f"看了 {looked} 页，" if looked else ""
    return f"{head}{len(notes)} 处版式问题（第 {pages} 页）"

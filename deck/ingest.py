"""Ingest: turn a folder of whatever the user has into one bundle.

This is moat #1. Every other AI deck tool starts from a sentence. Work starts
from a folder: three spreadsheets, a Word report, some meeting notes and a
handful of photos the boss sent at 11pm.

Nothing here calls a model. Reading files is deterministic and should stay
that way — it is the part that must never be wrong.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

TABLE_EXT = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv"}
DOC_EXT = {".docx", ".md", ".txt"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class Table:
    name: str          # "销售数据.xlsx / 月度销售"
    df: pd.DataFrame
    source: str        # absolute path
    sheet: str | None = None


@dataclass
class Doc:
    name: str
    text: str
    source: str


@dataclass
class Picture:
    id: str
    name: str
    path: str
    width: int
    height: int
    caption: str = ""      # set by the vision pass, empty until then


@dataclass
class Bundle:
    tables: list[Table] = field(default_factory=list)
    docs: list[Doc] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.tables:
            bits.append(f"{len(self.tables)} 个表")
        if self.docs:
            bits.append(f"{len(self.docs)} 份文档")
        if self.pictures:
            bits.append(f"{len(self.pictures)} 张图")
        return "，".join(bits) or "什么都没读到"


def _read_csv(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            sep = "\t" if path.suffix.lower() == ".tsv" else ","
            return pd.read_csv(path, encoding=enc, sep=sep)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法解码 {path.name}")


def _read_excel(path: Path) -> list[Table]:
    out: list[Table] = []
    sheets = pd.read_excel(path, sheet_name=None, header=0)
    for sheet, df in sheets.items():
        if df.empty or len(df.columns) < 2:
            continue
        df = df.dropna(axis=1, how="all")
        if len(df.columns) < 2:
            continue
        out.append(Table(name=f"{path.name} / {sheet}", df=df, source=str(path), sheet=str(sheet)))
    return out


def _read_docx(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    parts = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _picture_id(i: int) -> str:
    return f"p{i + 1}"


def load(path: str | Path, *, max_docs: int = 12, max_pics: int = 24) -> Bundle:
    root = Path(path)
    b = Bundle()
    if root.is_file():
        files = [root]
        root = root.parent
    else:
        files = sorted(p for p in root.rglob("*") if p.is_file())

    for p in files:
        if p.name.startswith(".") or p.name.startswith("~$"):
            continue
        ext = p.suffix.lower()
        try:
            if ext in TABLE_EXT:
                if ext in {".xlsx", ".xlsm", ".xls"}:
                    b.tables.extend(_read_excel(p))
                else:
                    df = _read_csv(p)
                    if not df.empty and len(df.columns) >= 2:
                        b.tables.append(Table(name=p.name, df=df, source=str(p)))
            elif ext in DOC_EXT:
                if len(b.docs) >= max_docs:
                    continue
                text = _read_docx(p) if ext == ".docx" else p.read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    b.docs.append(Doc(name=p.name, text=text.strip(), source=str(p)))
            elif ext in IMAGE_EXT:
                if len(b.pictures) >= max_pics:
                    continue
                from PIL import Image

                with Image.open(p) as im:
                    w, h = im.size
                b.pictures.append(
                    Picture(id=_picture_id(len(b.pictures)), name=p.name, path=str(p), width=w, height=h)
                )
            else:
                b.skipped.append(p.name)
        except Exception as e:  # noqa: BLE001 - one bad file must not kill the run
            b.skipped.append(f"{p.name} ({type(e).__name__})")
    return b

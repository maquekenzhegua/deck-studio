"""Ingest: turn a folder of whatever the user has into one bundle.

This is moat #1. Every other AI deck tool starts from a sentence. Work starts
from a folder: three spreadsheets, a Word report, some meeting notes and a
handful of photos the boss sent at 11pm.

Nothing here calls a model. Reading files is deterministic and should stay
that way — it is the part that must never be wrong.
"""

from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

TABLE_EXT = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv"}
DOC_EXT = {".docx", ".md", ".txt"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
PDF_EXT = {".pdf"}
DECK_EXT = {".pptx"}


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
class Outline:
    """The shape of a deck that already exists.

    "按去年那个格式" is the most common brief in the world and it points at a
    file, not a sentence. Keeping the page-by-page shape is the difference
    between reading that file and merely opening it.
    """
    source: str
    kind: str                  # "pptx" | "pdf"
    pages: list[str] = field(default_factory=list)   # one label per page

    def block(self) -> str:
        if not self.pages:
            return ""
        head = f"{Path(self.source).name}（{len(self.pages)} 页）"
        return head + "\n" + "\n".join(f"  {i:02d} {t}" for i, t in enumerate(self.pages, 1))


@dataclass
class Bundle:
    tables: list[Table] = field(default_factory=list)
    docs: list[Doc] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)
    outlines: list[Outline] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.tables:
            bits.append(f"{len(self.tables)} 个表")
        if self.docs:
            bits.append(f"{len(self.docs)} 份文档")
        if self.pictures:
            bits.append(f"{len(self.pictures)} 张图")
        if self.outlines:
            bits.append(f"{len(self.outlines)} 份现成 deck")
        return "，".join(bits) or "什么都没读到"

    def outline_block(self) -> str:
        blocks = [o.block() for o in self.outlines if o.pages]
        if not blocks:
            return ""
        return ("## 已有的 deck 结构（用户说「按这个格式」时指的就是它）\n"
                + "\n\n".join(blocks))


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



def _pdf_pages(path: Path) -> tuple[str, list[Table]]:
    """Text per page, plus tables when a table extractor is available.

    PyMuPDF first: it resolves CJK font encodings, and pypdf on a CJK PDF
    returns the raw glyph codes as mojibake — text that looks present and is
    useless. pypdf stays as the fallback for Latin PDFs so the dependency is
    optional rather than required.
    """
    pages: list[str] = []
    tables: list[Table] = []

    try:
        import fitz  # PyMuPDF

        with fitz.open(str(path)) as doc:
            for pno, page in enumerate(doc, 1):
                text = "\n".join(ln.rstrip() for ln in page.get_text().splitlines() if ln.strip())
                if text:
                    pages.append(f"--- 第 {pno} 页 ---\n{text}")
                try:
                    found = page.find_tables()
                except Exception:  # noqa: BLE001
                    continue
                for tno, tbl in enumerate(getattr(found, "tables", []), 1):
                    rows = tbl.extract()
                    if not rows or len(rows) < 2:
                        continue
                    width = max(len(r) for r in rows)
                    rows = [list(r) + [None] * (width - len(r)) for r in rows]
                    df = pd.DataFrame(rows[1:], columns=[str(c) for c in rows[0]])
                    df = df.dropna(axis=1, how="all")
                    if len(df.columns) < 2 or df.empty:
                        continue
                    tables.append(Table(name=f"{path.name} / 第{pno}页表{tno}",
                                        df=df, source=str(path), sheet=f"p{pno}"))
        return "\n\n".join(pages), tables
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        pass

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one broken page must not lose the file
            text = ""
        text = "\n".join(ln.rstrip() for ln in text.splitlines() if ln.strip())
        if text:
            pages.append(f"--- 第 {i} 页 ---\n{text}")
    return "\n\n".join(pages), tables


def _pptx(path: Path, pics: list[Picture], max_pics: int, out_dir: Path) -> tuple[str, list[Table], Outline]:
    """Slide text, slide tables, embedded pictures, and the page-by-page shape."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(path))
    texts: list[str] = []
    tables: list[Table] = []
    labels: list[str] = []

    for sno, slide in enumerate(prs.slides, 1):
        body: list[str] = []
        title = ""
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = "\n".join(p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip())
                if not t:
                    continue
                # The title is the topmost non-empty text, not the first one in
                # shape order: pptx stores shapes in z-order, not reading order.
                if not title and (shape.top or 0) < (prs.slide_height or 1) * 0.25:
                    title = t.splitlines()[0][:60]
                body.append(t)
            elif getattr(shape, "has_table", False) and shape.has_table:
                rows = [[c.text.strip() for c in r.cells] for r in shape.table.rows]
                if len(rows) >= 2 and len(rows[0]) >= 2:
                    df = pd.DataFrame(rows[1:], columns=rows[0])
                    df = df.dropna(axis=1, how="all")
                    if len(df.columns) >= 2 and not df.empty:
                        tables.append(Table(name=f"{path.name} / 第{sno}页表",
                                            df=df, source=str(path), sheet=f"s{sno}"))
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE and len(pics) < max_pics:
                try:
                    blob = shape.image.blob
                    ext = shape.image.ext or "png"
                    dest = out_dir / f"{path.stem}-s{sno}-{len(pics) + 1}.{ext}"
                    dest.write_bytes(blob)
                    from PIL import Image

                    with Image.open(dest) as im:
                        w, h = im.size
                    pics.append(Picture(id=_picture_id(len(pics)), name=dest.name,
                                        path=str(dest), width=w, height=h))
                except Exception:  # noqa: BLE001
                    pass
        if body:
            texts.append(f"--- 第 {sno} 页 ---\n" + "\n".join(body))
        labels.append(title or (body[0].splitlines()[0][:40] if body else f"第 {sno} 页"))

    outline = Outline(source=str(path), kind="pptx", pages=labels)
    return "\n\n".join(texts), tables, outline


def _picture_id(i: int) -> str:
    return f"p{i + 1}"


def load(path: str | Path, *, max_docs: int = 12, max_pics: int = 24) -> Bundle:
    root = Path(path)
    b = Bundle()
    pic_dir = Path(tempfile.mkdtemp(prefix="deck-pics-"))
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
            elif ext in PDF_EXT:
                if len(b.docs) >= max_docs:
                    continue
                text, tabs = _pdf_pages(p)
                b.tables.extend(tabs)
                if text.strip():
                    b.docs.append(Doc(name=p.name, text=text.strip(), source=str(p)))
            elif ext in DECK_EXT:
                text, tabs, outline = _pptx(p, b.pictures, max_pics, pic_dir)
                b.tables.extend(tabs)
                b.outlines.append(outline)
                if text.strip() and len(b.docs) < max_docs:
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

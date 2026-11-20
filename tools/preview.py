"""Preview a saved plan under one or more themes and export page images.

Why this exists: `deck build` costs a model call, and judging a visual system
from the XML is impossible. This renders a plan JSON directly, then asks the
installed PowerPoint to export every slide at 1600x900, then tiles the pages
into one contact sheet so a whole deck can be judged at a glance.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deck import compute, ingest, render2  # noqa: E402
from deck.plan import Plan  # noqa: E402


def _com_ready():
    """COM is per-thread: FastAPI serves requests from a worker pool, so a page
    that renders in one request and exports in the next needs its own
    CoInitialize or PowerPoint refuses with 'CoInitialize has not been called'."""
    try:
        import pythoncom  # noqa: PLC0415

        pythoncom.CoInitialize()
        return pythoncom
    except Exception:
        return None


def _powerpoint(pptx: Path, tries: int = 3):
    """Open a deck in PowerPoint, surviving a busy server.

    RPC_E_SERVERCALL_RETRYLATER shows up when another automation instance is
    still shutting down; the call then returns a half-built proxy whose
    `.Slides` is missing. Retry instead of exporting a corrupt preview.
    """
    import time  # noqa: PLC0415

    import pythoncom  # noqa: PLC0415
    import win32com.client  # noqa: PLC0415

    last: Exception | None = None
    for attempt in range(tries):
        app = win32com.client.Dispatch("PowerPoint.Application")
        try:
            pres = app.Presentations.Open(
                str(pptx.resolve()), ReadOnly=True, WithWindow=False)
            _ = pres.Slides.Count           # prove the proxy is real
            return app, pres
        except Exception as exc:            # noqa: BLE001
            last = exc
            try:
                app.Quit()
            except Exception:               # noqa: BLE001
                pass
            pythoncom.CoFreeUnusedLibraries()
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"PowerPoint 没能打开这份文件：{last}")


def soffice() -> str | None:
    """LibreOffice, if present: the only headless converter that does not need
    a window, a login or a COM apartment. Office/WPS automation is the fallback."""
    import os  # noqa: PLC0415

    env = os.environ.get("DECK_SOFFICE")
    if env and Path(env).exists():
        return env
    for cand in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        r"D:\LibreOffice\program\soffice.exe",
    ):
        if Path(cand).exists():
            return cand
    return shutil.which("soffice")


def _lo_pdf(pptx: Path) -> Path | None:
    """PPTX -> PDF with LibreOffice, in a private profile so a running
    LibreOffice window never blocks the conversion."""
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    exe = soffice()
    if not exe:
        return None
    work = Path(tempfile.mkdtemp(prefix="deck-lo-"))
    profile = work / "profile"
    cmd = [exe, "--headless", "--norestore", "--nolockcheck", "--nodefault",
           f"-env:UserInstallation=file:///{profile.as_posix()}",
           "--convert-to", "pdf", "--outdir", str(work), str(pptx.resolve())]
    try:
        subprocess.run(cmd, check=True, timeout=180,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:                        # noqa: BLE001
        return None
    pdf = work / (pptx.stem + ".pdf")
    return pdf if pdf.exists() else None


def export_png_lo(pptx: Path, out_dir: Path, width: int = 1600,
                  height: int = 900) -> list[Path]:
    """Render slides from the LibreOffice PDF. Fidelity is high because our
    decks are plain shapes, text and pictures - no SmartArt, no effects."""
    try:
        import pymupdf as fitz  # noqa: PLC0415
    except ImportError:  # pragma: no cover - older wheels
        import fitz  # noqa: PLC0415

    pdf = _lo_pdf(pptx)
    if pdf is None:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    made: list[Path] = []
    with fitz.open(pdf) as doc:
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=fitz.Matrix(width / page.rect.width,
                                                     height / page.rect.height),
                                  alpha=False)
            target = out_dir / f"{i:02d}.png"
            pix.save(str(target))
            made.append(target)
    return made


def export_png(pptx: Path, out_dir: Path, width: int = 1600, height: int = 900) -> list[Path]:
    made = export_png_lo(pptx, out_dir, width, height)
    if made:
        return made
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    com = _com_ready()
    made: list[Path] = []
    try:
        app, pres = _powerpoint(pptx)
        try:
            for i, slide in enumerate(pres.Slides, 1):
                target = out_dir / f"{i:02d}.png"
                slide.Export(str(target.resolve()), "PNG", width, height)
                made.append(target)
        finally:
            pres.Close()
            app.Quit()
    finally:
        if com is not None:
            com.CoUninitialize()
    return made


def export_pdf(pptx: Path, pdf: Path) -> Path:
    """A deck that cannot be sent is not finished. PowerPoint's PDF export is
    the only faithful one on a machine without LibreOffice."""
    pdf.parent.mkdir(parents=True, exist_ok=True)
    if pdf.exists():
        pdf.unlink()
    via_lo = _lo_pdf(pptx)
    if via_lo is not None:
        shutil.copyfile(via_lo, pdf)
        return pdf
    com = _com_ready()
    try:
        app, pres = _powerpoint(pptx)
        try:
            pres.SaveAs(str(pdf.resolve()), 32)  # ppSaveAsPDF
        finally:
            pres.Close()
            app.Quit()
    finally:
        if com is not None:
            com.CoUninitialize()
    return pdf


def contact_sheet(pages: list[Path], out: Path, cols: int = 4, gap: int = 12) -> Path:
    if not pages:
        return out
    with Image.open(pages[0]) as first:
        w, h = first.size
    scale = 480 / w
    tw, th = int(w * scale), int(h * scale)
    rows = (len(pages) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * gap,
                              rows * th + (rows + 1) * gap), "#20242B")
    for i, p in enumerate(pages):
        with Image.open(p) as im:
            im = im.convert("RGB").resize((tw, th), Image.LANCZOS)
            x = gap + (i % cols) * (tw + gap)
            y = gap + (i // cols) * (th + gap)
            sheet.paste(im, (x, y))
    sheet.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--input", default="samples/data")
    ap.add_argument("--out", default="out/preview")
    ap.add_argument("--theme", default="navy")
    ap.add_argument("--photo", default=None, help="extra photo fixture, bound as px")
    args = ap.parse_args()

    bundle = ingest.load(Path(args.input))
    if args.photo:
        from PIL import Image
        with Image.open(args.photo) as im:
            iw, ih = im.size
        bundle.pictures.append(ingest.Picture(
            id="px", name=Path(args.photo).stem, path=str(Path(args.photo).resolve()),
            width=iw, height=ih, caption="现场照片（布局样例）"))
    facts = compute.extract(bundle)
    plan = Plan.from_json(Path(args.plan).read_text(encoding="utf-8"))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pptx = out / f"{args.theme}.pptx"
    render2.build(plan, facts, bundle.pictures, str(pptx), theme=args.theme)
    pages = export_png(pptx, out / args.theme)
    sheet = contact_sheet(pages, out / f"sheet-{args.theme}.png")
    print(sheet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

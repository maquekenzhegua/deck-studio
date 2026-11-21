"""Deck Studio server.

The CLI is the engine; this is the cockpit.  One local process, one user, one
working directory per launch.  Everything the UI needs is expressed as JSON so
the same routes can be driven by the desktop shell or a browser tab.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import paths, settings  # noqa: E402
from deck import (agent, compose, compute, edit, eyes, ingest, multimodal,  # noqa: E402
                  readiness, render2, review as reviewmod, tighten, verify)
from deck.llm import Client, LLMError  # noqa: E402
from deck.plan import LAYOUTS, Plan, Slide, _validate  # noqa: E402
from deck.visual import THEMES  # noqa: E402

STATIC = paths.resource("app", "static")
if not STATIC.exists():  # source checkout fallback
    STATIC = Path(__file__).resolve().parent / "static"
_DIRS = paths.ensure()
HOME = _DIRS["home"]
UPLOADS = _DIRS["uploads"]
OUT = _DIRS["out"]
MEDIA = _DIRS["media"]

STATE: dict[str, Any] = {
    "bundle": None,
    "facts": [],
    "plan": None,
    "theme": "editorial",
    "pptx": None,
    "pages": [],
    "qa": None,
    "started": time.time(),
    "client": None,
    "auto": {"running": False, "step": "", "log": [], "done": False, "error": ""},
}
PLAN_FILE = HOME / "plan.json"


def _save_plan(plan: Plan | None, planner: dict | None, theme: str | None) -> None:
    """The outline outlives the window.

    A desktop app that forgets a ten-minute model plan because it was closed is
    not a desktop app. Written atomically so a crash mid-write cannot leave a
    half-JSON that makes the next launch look broken.
    """
    if plan is None:
        return
    data = {"plan": asdict(plan), "planner": planner or {}, "theme": theme or ""}
    tmp = PLAN_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PLAN_FILE)
    except OSError as exc:
        print(f"[deck] plan save skipped: {exc}", file=sys.stderr)


def _read_plan(facts, pictures) -> tuple[Plan | None, dict, str]:
    if not PLAN_FILE.exists():
        return None, {}, ""
    try:
        data = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
        plan = _validate(data.get("plan") or {}, facts, pictures, None)
        if not plan.slides:
            return None, {}, ""
        return plan, data.get("planner") or {}, data.get("theme") or ""
    except Exception as exc:  # noqa: BLE001 - a stale file must not block startup
        print(f"[deck] plan restore skipped: {exc}", file=sys.stderr)
        return None, {}, ""


LOCK = threading.RLock()

THEME_COPY = {
    "navy": ("海军蓝", "克制、可信，适合经营会"),
    "editorial": ("编辑部", "米色纸与衬线，适合战略叙事"),
    "tech": ("技术蓝", "冷白与电光蓝，适合产品发布"),
    "forest": ("森林", "墨绿与琥珀，适合可持续议题"),
    "midnight": ("午夜", "全暗场，适合舞台与路演"),
}


# ---------------------------------------------------------------- serialisation

def _human(label: str) -> str:
    """Fact labels are built for code ("销售额 按 区域（求和）"). The rail is read
    by a person, so drop the pivot grammar and the aggregate hint."""
    text = re.sub(r"\s*按\s*", " · ", label or "")
    text = re.sub(r"（(求和|均值|合计|最大|最小|计数)）$", "", text)
    text = re.sub(r"（\s*逐期\s*）", "", text)
    return text.strip()


AGG_CN = {"sum": "合计", "mean": "均值", "max": "最高", "min": "最低", "count": "计数"}


def fact_json(f) -> dict:
    label = _human(f.label)
    pts = getattr(f, "points", None) or []
    if pts:
        # A distribution and its total used to share one label, so the rail
        # showed two rows that looked like duplicates.
        label = f"{label}（{len(pts)} 项）"
    elif getattr(f, "agg", "") in AGG_CN:
        label = f"{label}（{AGG_CN[f.agg]}）"
    return {
        "id": f.id,
        "label": label,
        "value": f.value,
        "raw": f.raw,
        "unit": f.unit,
        "kind": f.kind,
        "chart": f.chart,
        "source": f.source,
        "change": f.change,
        "top": f.top,
    }


def plan_json(p: Plan | None) -> dict | None:
    if p is None:
        return None
    return {
        "deck_title": p.deck_title,
        "subtitle": p.subtitle,
        "slides": [asdict(s) for s in p.slides],
    }


def plan_from(payload: dict) -> Plan:
    slides = []
    for raw in payload.get("slides", []):
        layout = raw.get("layout", "bullets")
        if layout not in LAYOUTS:
            layout = "bullets"
        bullets = [str(b) for b in (raw.get("bullets") or [])][:6]
        slides.append(
            Slide(
                layout=layout,
                title=str(raw.get("title", "")).strip() or "未命名页",
                fact=raw.get("fact") or None,
                image=raw.get("image") or None,
                note=str(raw.get("note") or ""),
                bullets=bullets,
                variant=str(raw.get("variant") or ""),
                script=str(raw.get("script") or ""),
            )
        )
    return Plan(
        deck_title=str(payload.get("deck_title", "")).strip() or "未命名汇报",
        subtitle=str(payload.get("subtitle", "")),
        slides=slides,
    )


def adopt_template(bundle) -> None:
    """Register the uploaded deck's own colour scheme as a selectable theme."""
    themes = (getattr(bundle, "meta", {}) or {}).get("pptx_themes") or []
    if not themes:
        return
    try:
        from deck import palette  # noqa: PLC0415
        from deck.visual import THEMES  # noqa: PLC0415

        t = themes[0]
        THEMES["custom"] = palette.build(t.get("colors") or {}, t.get("fonts") or {},
                                            title_pt=float(t.get("title_pt") or 0))
        THEME_COPY["custom"] = ("你的模板", f"取自 {t.get('file', 'PPTX')} 的配色与字体")
    except Exception as exc:  # noqa: BLE001
        print(f"[deck] template palette skipped: {exc}", file=sys.stderr)



def client_for_model() -> Client | None:
    """A client only when the user actually configured one."""
    cfg = settings.load()
    # The settings file is the UI's way in; DECK_* is the deployment's way in
    # (CI, a shared machine, a container). The file wins when both exist, but
    # an empty file must not shadow a perfectly good environment.
    base = cfg["base_url"] or os.environ.get("DECK_BASE_URL", "")
    model = cfg["model"] or os.environ.get("DECK_MODEL", "")
    key = cfg["api_key"] or os.environ.get("DECK_API_KEY", "")
    if not (base and model and key):
        return None
    # One bounded attempt per call: the endpoint runs at roughly 28 tokens a
    # second here, so a six-page outline needs 90-130s. Retrying inside the
    # client only doubles the wait without changing the reason for it; the
    # planner's own "ask for fewer pages" retry is the useful one.
    try:
        return Client(model=model, base_url=base, api_key=key,
                      timeout=int(os.environ.get("DECK_LLM_TIMEOUT") or 200), attempts=1)
    except (LLMError, Exception):  # noqa: BLE001 - a bad key must not break upload
        return None


def client_for_vision() -> Client | None:
    """The eyes are a separate model, and absent means absent.

    A text-only endpoint answers 404 on an image, so falling back to the text
    model would turn "look at this" into a confusing failure.
    """
    cfg = settings.load()
    base = cfg["vision_url"] or cfg["base_url"] or os.environ.get("DECK_VISION_URL", "")
    model = cfg["vision_model"] or os.environ.get("DECK_VISION_MODEL", "")
    key = cfg["vision_key"] or cfg["api_key"] or os.environ.get("DECK_VISION_KEY", "")
    if not (base and model and key):
        return None
    try:
        return Client(model=model, base_url=base, api_key=key, timeout=120, attempts=1)
    except Exception:  # noqa: BLE001
        return None


def persist() -> None:
    with LOCK:
        _save_plan(STATE.get("plan"), STATE.get("planner"), STATE.get("theme"))


def reload_bundle(use_model: bool = False, restore: bool = False) -> None:
    """Read the workspace and draft an outline.

    Uploading must answer immediately: a model plan takes 30–150 seconds, and
    making the file drop wait for it looks like a hang. So a reload drafts
    offline; the explicit "生成" (`/api/plan`) is where the model takes over.
    """
    bundle = ingest.load(UPLOADS)
    multimodal.augment(bundle, MEDIA, pages=True)
    adopt_template(bundle)
    facts = compute.extract(bundle)
    saved, saved_planner, saved_theme = _read_plan(facts, bundle.pictures) if restore else (None, {}, "")
    if saved is not None:
        # Reopen the deck the user was working on, not a fresh draft of the same
        # files: a model outline can cost minutes and a dozen small edits.
        plan, planner = saved, saved_planner or {"who": "restored", "label": "已恢复上次的大纲", "log": []}
        result = None
    else:
        result = agent.plan(bundle, facts, client=client_for_model() if use_model else None)
        plan = result.plan
        planner = {"who": result.planner, "model": result.model,
                   "label": result.summary(), "log": result.log}
    with LOCK:
        STATE["bundle"] = bundle
        STATE["facts"] = facts
        STATE["plan"] = plan
        STATE["planner"] = planner
        if saved_theme in THEMES:
            STATE["theme"] = saved_theme
        STATE["pptx"] = None
        STATE["pages"] = []
        STATE["qa"] = None
    if result is not None:
        persist()


def readiness_now() -> dict:
    with LOCK:
        bundle, facts = STATE["bundle"], STATE["facts"]
    try:
        return readiness.audit(bundle, facts).as_dict()
    except Exception as exc:  # noqa: BLE001
        print(f"[deck] readiness skipped: {exc}", file=sys.stderr)
        return {}


def review_now() -> dict:
    """The outline, read the way an editor would read it."""
    plan = STATE["plan"]
    if plan is None:
        return {"findings": [], "summary": ""}
    found = reviewmod.review(plan, STATE["facts"])
    return {"findings": [f.as_dict() for f in found],
            "summary": reviewmod.summary(found)}


def _planner_view() -> dict:
    """What the outline in front of you actually is.

    A restored workspace holds an offline draft; if a model is configured the
    honest label is "draft, the model has not re-ordered it yet", not
    "offline planner" with a header that says a model is connected.
    """
    planner = dict(STATE.get("planner") or {})
    mode = settings.public().get("mode")
    if mode == "model" and planner.get("who") != "model":
        planner["label"] = "离线草稿 · 可重排"
        planner["who"] = "offline-draft"
    return planner


def snapshot() -> dict:
    with LOCK:
        bundle = STATE["bundle"]
        files = []
        if bundle is not None:
            seen = set()
            for item in [*bundle.tables, *bundle.docs, *bundle.pictures, *bundle.outlines]:
                src = Path(getattr(item, "source", None) or getattr(item, "path", ""))
                if src.name in seen or not str(src).startswith(str(UPLOADS)):
                    continue
                seen.add(src.name)
                files.append({
                    "name": src.name,
                    "ext": src.suffix.lower().lstrip("."),
                    "size": src.stat().st_size if src.exists() else 0,
                })
        return {
            "files": files,
            "facts": [fact_json(f) for f in STATE["facts"]],
            "plan": plan_json(STATE["plan"]),
            "home": str(HOME),
            "client": STATE.get("client"),
            "theme": STATE["theme"],
            "pages": STATE["pages"],
            "pptx": f"/files/{Path(STATE['pptx']).name}" if STATE["pptx"] else None,
            "skipped": list(getattr(bundle, "skipped", []) or []) if bundle else [],
            "pictures": [
                {"id": p.id, "name": p.name, "kind": p.kind, "caption": p.caption,
                 "note": p.note, "width": p.width, "height": p.height,
                 "palette": p.palette, "url": f"/api/picture/{p.id}"}
                for p in (getattr(bundle, "pictures", []) if bundle else [])
            ],
            "qa": STATE["qa"],
            "review": review_now(),
            "readiness": readiness_now(),
            "planner": _planner_view(),
            "settings": settings.public(),
            "meta": list(getattr(bundle, "meta", {}) or {}) if bundle else [],
        }


# ---------------------------------------------------------------- app

class BuildBody(BaseModel):
    plan: dict
    theme: str = "navy"
    force: bool = False


class AutoBody(BaseModel):
    theme: str = ""
    slides: int = 0


class PlanBody(BaseModel):
    brief: str = ""
    deck_title: str = ""
    subtitle: str = ""
    slides: int = 0


class ExportBody(BaseModel):
    target: str = "desktop"


class SettingsBody(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    vision_url: str = ""
    vision_key: str = ""
    vision_model: str = ""


class ThemeBody(BaseModel):
    theme: str


class EditBody(BaseModel):
    text: str = ""


def create_app() -> FastAPI:
    app = FastAPI(title="Deck Studio", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.on_event("startup")
    def _restore():
        """Reopen the last workspace instead of starting from an empty desk."""
        try:
            settings.apply()
        except Exception as exc:  # noqa: BLE001
            print(f"[deck] settings skipped: {exc}")
        try:
            if any(UPLOADS.iterdir()):
                reload_bundle(restore=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[deck] workspace restore skipped: {exc}")

    @app.get("/api/client")
    def client(w: int = 0, h: int = 0, dpr: float = 1):
        STATE["client"] = {"w": w, "h": h, "dpr": dpr}
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True, "uptime": round(time.time() - STATE["started"], 1)}

    @app.get("/api/state")
    def state():
        return snapshot()

    @app.get("/api/picture/{pid}")
    def picture(pid: str):
        with LOCK:
            bundle = STATE["bundle"]
        for p in (getattr(bundle, "pictures", []) if bundle else []):
            if p.id == pid and Path(p.path).exists():
                return FileResponse(p.path)
        raise HTTPException(404, "not found")

    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...)):
        saved = []
        for up in files:
            name = Path(up.filename or "upload.bin").name
            name = re.sub(r"[^\w.\-\u4e00-\u9fff ()]+", "_", name)
            target = UPLOADS / name
            stem, suffix = target.stem, target.suffix
            n = 2
            while target.exists():
                target = UPLOADS / f"{stem}-{n}{suffix}"
                n += 1
            with target.open("wb") as fh:
                shutil.copyfileobj(up.file, fh)
            saved.append(target.name)
        try:
            reload_bundle()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"读取失败：{exc}") from exc
        body = snapshot()
        # A file can be copied and still be unreadable: report what actually
        # produced content, so the window never lists a file that yielded nothing.
        bundle = STATE.get("bundle")
        used: set[str] = set()
        if bundle is not None:
            for group in (bundle.tables, bundle.docs, bundle.pictures, bundle.outlines):
                for item in group:
                    src = getattr(item, "source", None) or getattr(item, "path", "")
                    used.add(Path(str(src)).name)
        body["saved"] = [n for n in saved if n in used]
        body["skipped"] = [n for n in saved if n not in used]
        return body

    @app.post("/api/sample")
    def sample():
        sample_dir = paths.resource("samples", "data")
        if not sample_dir.exists():
            sample_dir = ROOT / "samples" / "data"
        if not sample_dir.exists():
            raise HTTPException(404, "示例数据不存在")
        for p in sample_dir.iterdir():
            if p.is_file():
                shutil.copy2(p, UPLOADS / p.name)
        reload_bundle()
        return snapshot()

    @app.post("/api/ideas")
    def ideas():
        with LOCK:
            bundle, facts = STATE["bundle"], STATE["facts"]
        if bundle is None or not facts:
            raise HTTPException(409, "先传一份有数据的材料")
        client = client_for_model()
        if client is None:
            raise HTTPException(409, "先在「模型」里填一个端点，模型才能提出自己的角度")
        try:
            return {"ideas": agent.pitch(bundle, facts, client=client)}
        except LLMError as exc:
            raise HTTPException(502, f"模型没有回话：{str(exc)[:120]}")
        except ValueError as exc:
            raise HTTPException(502, str(exc))

    @app.post("/api/notes")
    def write_notes():
        with LOCK:
            plan, facts = STATE["plan"], STATE["facts"]
        if plan is None or not plan.slides:
            raise HTTPException(409, "先生成一版大纲")
        client = client_for_model()
        if client is None:
            raise HTTPException(409, "先在「模型」里填一个端点，模型才能写讲稿")
        got = agent.notes(plan, facts, client=client)
        if got is None:
            raise HTTPException(502, "模型这次没写出可用的讲稿，再试一次")
        new_plan, message = got
        with LOCK:
            STATE["plan"] = new_plan
        return {"plan": asdict(new_plan), "message": message,
                "review": review_now(), "readiness": readiness_now()}

    @app.post("/api/plan")
    def replan(body: PlanBody):
        with LOCK:
            bundle, facts = STATE["bundle"], STATE["facts"]
        if bundle is None:
            raise HTTPException(409, "先上传资料")
        text_model = settings.load()["model"] if settings.load()["base_url"] else ""
        ask = body.brief or ""
        result = agent.plan(bundle, facts, ask, deck_title=body.deck_title or None,
                            subtitle=body.subtitle, slides=body.slides,
                            client=client_for_model())
        plan = result.plan
        if body.subtitle:
            plan.subtitle = body.subtitle
        with LOCK:
            STATE["plan"] = plan
            STATE["planner"] = {"who": result.planner, "model": result.model,
                                "label": result.summary(), "log": result.log}
        persist()
        return {"plan": plan_json(plan), "planner": STATE["planner"],
                "review": review_now(), "readiness": readiness_now()}

    @app.post("/api/build")
    def build(body: BuildBody):
        with LOCK:
            facts = STATE["facts"]
            pictures = list(getattr(STATE["bundle"], "pictures", []) or [])
            theme = body.theme if body.theme in THEMES else "navy"
        if not facts:
            # Documents and pictures still deserve a deck: extracts, or the
            # scene itself. No charts, because there is nothing to chart.
            bundle = STATE["bundle"]
            docs = list(getattr(bundle, "docs", []) or [])
            pics = list(getattr(bundle, "pictures", []) or [])
            if not docs and not pics:
                raise HTTPException(409, "这份材料里既没有表格，也没有正文和图片")
        plan = plan_from(body.plan) if body.plan and body.plan.get("slides") else None
        if plan is None and not facts:
            plan = compose.draft_text(bundle) if docs else compose.draft_pictures(bundle)
        if plan is None:
            plan = plan_from(body.plan)
        if not plan.slides:
            raise HTTPException(422, "大纲是空的")
        problems = [p.__dict__ for p in verify.check(plan, facts)]
        if problems and not body.force:
            return JSONResponse({"problems": problems}, status_code=422)

        stamp = time.strftime("%Y%m%d-%H%M%S")
        pptx = OUT / f"deck-{stamp}.pptx"
        try:
            render2.build(plan, facts, pictures, str(pptx), theme=theme)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"渲染失败：{exc}") from exc

        pages: list[str] = []
        png_dir: Path | None = None
        preview_error = ""
        try:
            from tools.preview import export_png

            png_dir = OUT / f"png-{stamp}"
            for p in export_png(pptx, png_dir, 1600, 900):
                pages.append(f"/files/{png_dir.name}/{p.name}")
        except Exception as exc:  # noqa: BLE001
            pages = []
            preview_error = str(exc)
            print(f"[deck] preview export skipped: {exc}", file=sys.stderr)

        qa: dict | None = None
        try:
            from tools.qa import check as qa_check

            qa = qa_check(pptx, png_dir if pages else None)
        except Exception as exc:  # noqa: BLE001
            print(f"[deck] qa skipped: {exc}", file=sys.stderr)

        with LOCK:
            STATE.update(plan=plan, theme=theme, pptx=str(pptx), pages=pages, qa=qa)
        persist()
        return {
            "pptx": f"/files/{pptx.name}",
            "pages": pages,
            "problems": problems,
            "theme": theme,
            "plan": plan_json(plan),
            "qa": qa,
            "previewError": preview_error,
            "review": review_now(),
            "readiness": readiness_now(),
        }

    def _auto_note(step: str) -> None:
        box = STATE["auto"]
        box["step"] = step
        box["log"].append(step)

    def _auto_run(theme: str, slides: int) -> None:
        """The whole pipeline, in the order a person would do it.

        It uses the model where the model is good (deciding what to say) and
        the renderer where the renderer is good (deciding where it goes).
        Any failure is caught here: a background thread that dies silently
        would leave the UI spinning forever.
        """
        box = STATE["auto"]
        try:
            with LOCK:
                bundle, facts = STATE["bundle"], STATE["facts"]
                pics = list(getattr(bundle, "pictures", []) or [])
            if bundle is None:
                raise ValueError("先上传资料")
            client = client_for_model()
            brief = ""
            if client is not None:
                _auto_note("模型在读材料，找三个可以讲的角度")
                try:
                    ideas = agent.pitch(bundle, facts, client=client)
                    if ideas:
                        brief = f"{ideas[0]['angle']} {ideas[0]['why']}".strip()
                        box["log"].append(f"选了角度：{ideas[0]['title']}")
                except (LLMError, ValueError) as exc:
                    box["log"].append(f"提角度没成功，按材料本身排：{str(exc)[:80]}")
            _auto_note("在排页序" + ("（模型）" if client is not None else "（离线规则）"))
            result = agent.plan(bundle, facts, brief, slides=slides, client=client)
            plan = result.plan
            # Offline planning ignores the page budget; the control in the UI is
            # a promise, so trim here rather than silently returning sixteen.
            plan, cut = tighten.fit_pages(plan, slides)
            if cut:
                result.log.extend(cut)
                box["log"].extend(cut)
            with LOCK:
                STATE["plan"] = plan
                STATE["planner"] = {"who": result.planner, "model": result.model,
                                    "label": result.summary(), "log": result.log}
            if client is not None:
                _auto_note("在写讲稿")
                got = agent.notes(plan, facts, client=client)
                if got is not None:
                    plan, msg = got
                    with LOCK:
                        STATE["plan"] = plan
                    box["log"].append(msg)
            _auto_note("在排版、导预览、算质量分")
            theme2 = theme if theme in THEMES else STATE.get("theme") or "editorial"
            build(BuildBody(plan=asdict(plan), theme=theme2, force=True))
            box["done"] = True
            _auto_note("好了")
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not swallowed
            box["error"] = str(exc)[:200] or exc.__class__.__name__
            box["log"].append("出错：" + box["error"])
        finally:
            box["running"] = False

    @app.post("/api/auto")
    def auto(body: AutoBody):
        if STATE["auto"]["running"]:
            raise HTTPException(409, "正在跑，等这一版出来")
        with LOCK:
            STATE["auto"] = {"running": True, "step": "开始", "log": [], "done": False, "error": ""}
        th = threading.Thread(target=_auto_run, args=(body.theme, body.slides), daemon=True)
        th.start()
        return {"started": True}

    @app.get("/api/progress")
    def progress():
        return STATE["auto"]

    @app.get("/files/{path:path}")
    def files(path: str):
        target = (OUT / path).resolve()
        if not str(target).startswith(str(OUT.resolve())) or not target.exists():
            raise HTTPException(404, "not found")
        return FileResponse(target)

    @app.post("/api/export")
    def export(body: ExportBody):
        with LOCK:
            pptx = STATE["pptx"]
            pages = list(STATE["pages"])
        if not pptx:
            raise HTTPException(409, "先生成")
        src = Path(pptx)
        desk = Path.home() / "Desktop"
        if not desk.exists():
            desk = Path.home() / "OneDrive" / "Desktop"
        if not desk.exists():
            desk = Path.home() / "Downloads"
        desk.mkdir(parents=True, exist_ok=True)
        dest = desk / src.name
        shutil.copy2(src, dest)
        extra = ""
        pdf_path = ""
        if body.target == "pdf":
            from tools.preview import export_pdf  # noqa: PLC0415

            try:
                pdf_path = str(export_pdf(src, desk / f"{src.stem}.pdf"))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(503, f"PDF 导出需要本机 PowerPoint：{exc}") from exc
        if body.target == "png" and pages:
            zpath = desk / f"{src.stem}-png.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                for url in pages:
                    p = OUT / url.replace("/files/", "", 1)
                    if p.exists():
                        zf.write(p, p.name)
            extra = str(zpath)
        return {"path": str(dest), "png": extra, "pdf": pdf_path}


    @app.post("/api/theme")
    def set_theme(body: ThemeBody):
        """Choosing a theme is a state change even before anything is built."""
        if body.theme not in THEMES:
            raise HTTPException(400, "没有这个主题")
        with LOCK:
            STATE["theme"] = body.theme
        persist()
        return {"theme": body.theme}

    @app.post("/api/edit")
    def quick_edit(body: EditBody):
        """A sentence in, a changed outline out.

        With a model configured it is the editor: it can follow "把结论提前、
        删掉表格" the way a person would. The rule-based editor stays as the
        floor — it still handles the exact commands, and it takes over when
        the model is absent, slow, or returns a plan the rules reject.
        """
        with LOCK:
            plan = STATE["plan"]
            if plan is None:
                raise HTTPException(409, "先上传资料")
            facts = STATE.get("facts") or []
            text = body.text
            if text.strip() in ("按评审意见改", "按评审意见修改", "修评审问题"):
                findings = reviewmod.review(plan, facts)
                if not findings:
                    return {"plan": asdict(plan), "message": "评审没有发现问题，不用改。",
                            "theme": STATE.get("theme"), "changed": False,
                            "planner": STATE.get("planner"), "review": review_now(),
                "readiness": readiness_now()}
                text = reviewmod.as_prompt(findings)
            new_plan, message = edit.apply(plan, text)
            client = client_for_model()
            if client is not None and facts:
                got = agent.revise(plan, facts, text, client=client,
                                   pictures=(STATE["bundle"].pictures if STATE.get("bundle") else []))
                if got is not None:
                    new_plan, message = got
                    STATE["planner"] = {"who": "model", "model": client.model,
                                        "label": f"{client.model} 主导", "log": [message]}
            # One vocabulary, in one place: the endpoint must not keep its own
            # copy of the aliases or "换成模板" silently does nothing.
            tid = edit.theme_for(body.text)
            if tid:
                STATE["theme"] = tid
            STATE["plan"] = new_plan
            changed = len(new_plan.slides) != len(plan.slides) or new_plan != plan
        persist()
        return {"plan": asdict(new_plan), "message": message,
                "theme": STATE.get("theme"), "changed": bool(changed),
                "planner": STATE.get("planner"), "review": review_now(),
                "readiness": readiness_now()}

    @app.post("/api/notes")
    def write_notes():
        """The model writes what to say on each page, in its own words."""
        with LOCK:
            plan = STATE.get("plan")
            if plan is None:
                raise HTTPException(409, "先生成一版大纲")
            client = client_for_model()
            if client is None:
                raise HTTPException(409, "先在「模型」里填一个端点，再让它写讲稿")
            got = agent.notes(plan, STATE.get("facts") or [], client=client)
            if got is None:
                raise HTTPException(502, "模型这次没有写出可用的讲稿，稍后再试")
            plan, message = got
            STATE["plan"] = plan
        persist()
        return {"plan": asdict(plan), "message": message,
                "planner": STATE.get("planner")}

    @app.post("/api/look")
    def look_at_pages():
        """Let a vision model see the rendered deck and name what looks wrong.

        Structural checks already prove the numbers and the boxes. This is the
        only reader that judges composition, so its findings are layout notes,
        never content edits.
        """
        with LOCK:
            raw = list(STATE.get("pages") or [])
            # STATE keeps browser URLs ("/files/png-.../01.png"); the eyes need
            # files on disk. Translating here keeps one source of truth for the UI.
            pages = []
            for item in raw:
                text = str(item)
                path = OUT / text[len("/files/"):] if text.startswith("/files/") else Path(text)
                if path.exists():
                    pages.append(path)
            planner = STATE.get("planner")
        if not pages:
            raise HTTPException(409, "先生成一版，模型才有东西可看")
        client = client_for_vision()
        if client is None:
            raise HTTPException(409, "先在「模型」里填一个视觉模型，它才能看页面")
        try:
            found = eyes.look([Path(p) for p in pages], client)
        except LLMError as exc:
            raise HTTPException(502, f"视觉模型没有回应：{exc}") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "notes": [{"page": n.page, "problem": n.problem, "fix": n.fix} for n in found],
            "summary": eyes.summary(found, min(len(pages), eyes.MAX_TOTAL)),
            "prompt": eyes.as_prompt(found) if found else "",
            "planner": planner,
            "vision": client.model,
        }

    @app.post("/api/reveal")
    def reveal():
        os.startfile(str(OUT))  # type: ignore[attr-defined]
        return {"path": str(OUT)}

    @app.post("/api/open")
    def open_file(body: dict):
        path = body.get("path", "")
        if not path or not Path(path).exists():
            raise HTTPException(404, "文件不存在")
        os.startfile(path)  # type: ignore[attr-defined]
        return {"ok": True}


    @app.get("/api/settings")
    def get_settings():
        return settings.public()

    @app.post("/api/settings")
    def put_settings(body: SettingsBody):
        saved = settings.save(body.model_dump())
        STATE["settings"] = settings.public(saved)
        return {"ok": True, "settings": STATE["settings"]}

    @app.post("/api/settings/test")
    def test_settings(body: SettingsBody):
        data = settings.load()
        merged = {**data, **{k: v for k, v in body.model_dump().items() if v}}
        ok, message = settings.probe(
            merged.get("base_url", ""), merged.get("api_key", ""), merged.get("model", "")
        )
        return {"ok": ok, "message": message}

    @app.get("/api/meta")
    def meta():
        return {
            "themes": [
                {"id": k, "name": THEME_COPY.get(k, (k, ""))[0],
                 "note": THEME_COPY.get(k, (k, ""))[1],
                 "paper": v.paper, "ink": v.ink, "accent": v.accent,
                 "dark": v.dark}
                for k, v in THEMES.items()
                if k != "custom" or "custom" in THEME_COPY
            ],
            "layouts": sorted(LAYOUTS),
        }

    return app


app = create_app()

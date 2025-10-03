# backend/app/main.py

import json
from pathlib import Path
from urllib.parse import quote
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session
from sqlalchemy import func

from .settings import settings
from .db import Base, engine, get_db
from .models import FileAsset, ModelItem
from .routers import files as files_router
from .routers import tasks as tasks_router
from .routers import downloads as downloads_router
from .routers import extractions as extractions_router
from .routers import models as models_router
from .routers import export as export_router
from .routers import static_proxy as static_proxy_router
from .services.extractor_worker import extractor_worker
from .services.downloader_worker import downloader_worker
from .crawlers.scrape_session import aiohttp_hsd_session_manager

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = (BASE_DIR.parent).resolve()
EXTRACT_DIR = settings.WORKSPACE_DIR / "extractions"

# ── DevTools helper
def human_size(n: int | None) -> str:
    """以十進位(1000)換算：B, KB, MB, GB, TB, PB"""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return "-"
    if v < 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    val = float(v)
    i = 0
    while val >= 1000.0 and i < len(units) - 1:
        val /= 1000.0
        i += 1
    # 小數一位，去掉無意義的 .0
    num = f"{val:.1f}".rstrip("0").rstrip(".")
    return f"{num} {units[i]}"

def _setup_devtools_static() -> Path:               # 回傳 Path
    wk_dir = BASE_DIR / ".well-known" / "appspecific"
    wk_dir.mkdir(parents=True, exist_ok=True)       # ← 加 parents=True

    path = wk_dir / "com.chrome.devtools.json"
    if not path.exists():
        payload = {
            "workspace": {
                "root": str(PROJECT_ROOT).replace("\\", "/"),
                "uuid": "6ec0bd7f-11c0-43da-975e-2a8ad9ebae0b",
            }
        }
        path.write_text(                           # ← 先序列化為字串
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    return wk_dir                                   # ← 回傳目錄給 mount 用

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    if settings.DEBUG_DEVTOOLS:
        wk_dir = _setup_devtools_static()           # ← 取得 Path
        app.mount(
            "/.well-known/appspecific",
            StaticFiles(directory=str(wk_dir), html=False),
            name="devtools-wellknown",
        )
    await extractor_worker.start()
    await downloader_worker.start()
    try:
        yield
    finally:
        await extractor_worker.stop()
        await downloader_worker.stop()
        await aiohttp_hsd_session_manager.close_all_sessions()

app = FastAPI(title="Datasheet 校對系統", lifespan=lifespan)

@app.middleware("http")
async def no_cache_dev(request: Request, call_next):
    resp = await call_next(request)
    path = request.url.path

    if path.startswith("/static/"):
        # 靜態檔允許重新驗證（改了就能拿到新檔）
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    else:
        # HTML/JSON 一律不快取
        resp.headers["Cache-Control"] = "no-store"

    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

# Routers
app.include_router(files_router.router)
app.include_router(tasks_router.router)
app.include_router(downloads_router.router)
app.include_router(extractions_router.router)
app.include_router(models_router.router)
app.include_router(export_router.router)
app.include_router(static_proxy_router.router)

# 靜態與模板
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

templates_env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
    # 開發時可考慮 auto_reload=True
)

# 檔案大小顯示轉換器
templates_env.filters["human_size"] = human_size

def render_template(name: str, context: dict) -> HTMLResponse:
    tpl = templates_env.get_template(name)
    return HTMLResponse(tpl.render(**context))

# pages
@app.get("/", include_in_schema=False)
async def index(request: Request):
    return RedirectResponse(url=request.url_for("files_page"), status_code=307)

@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    return render_template("tasks.html", {"request": request})

@app.get("/files", response_class=HTMLResponse)
async def files_page(request: Request, db: Session = Depends(get_db)):
    files = db.query(FileAsset).order_by(FileAsset.created_at.desc()).all()

    # 一次收集已有的 extraction JSON（stem 就是 file_hash）
    existing_json_hashes = {p.stem for p in EXTRACT_DIR.glob("*.json")}
    parsed_map = {f.file_hash: (f.file_hash in existing_json_hashes) for f in files}

    return render_template(
        "files.html",
        {"request": request, "files": files, "parsed_map": parsed_map},
    )

@app.get("/files/{file_hash}", response_class=HTMLResponse)
async def file_detail(file_hash: str, request: Request, db: Session = Depends(get_db)):
    fa = db.get(FileAsset, file_hash)
    if not fa:
        raise HTTPException(status_code=404, detail="file not found")
    models = sorted(fa.models, key=lambda m: (m.model_number or ""))
    return render_template("file_detail.html", {"request": request, "fa": fa, "models": models})

@app.get("/review/{file_hash}", response_class=HTMLResponse)
async def review_file(request: Request, file_hash: str, db: Session = Depends(get_db)):
    fa = db.get(FileAsset, file_hash)
    if not fa:
        raise HTTPException(status_code=404, detail="file not found")

    model_numbers = sorted([m.model_number for m in fa.models])

    json_path = (EXTRACT_DIR / f"{file_hash}.json").as_posix()
    json_url = f"/api/static/?path={quote(json_path, safe='')}"

    return render_template(
        "review.html",
        {
            "request": request,
            "fa": fa,
            "model_numbers": model_numbers,
            "json_url": json_url,
        },
    )

@app.get("/pdf/{file_hash}")
async def serve_pdf(file_hash: str, db: Session = Depends(get_db)):
    fa = db.get(FileAsset, file_hash)
    if not fa:
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(fa.local_path, media_type="application/pdf")

# backend/app/routers/files.py

from __future__ import annotations

from pathlib import Path
from typing import List

import fitz  # PyMuPDF
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import FileAsset, FileModelAppearance, ModelItem
from ..routers.downloads import enqueue_urls
from ..schemas import (
    FileAssetLite,
    FileAssetOut,
    FilesPageOut,
    ModelItemOut,
    UploadMultiItemOut,
    UploadMultiOut,
)
from ..services.file_store import persist_bytes_to_store
from ..settings import settings

router = APIRouter(prefix="/api/files", tags=["files"])

EXTRACT_DIR = settings.WORKSPACE_DIR / "extractions"


def _norm_ws(s: str) -> str:
    return " ".join((s or "").replace("\u00a0", " ").split())


def _snippet_from_blocks(block_text: str, needle: str, context: int) -> str:
    text = _norm_ws(block_text)
    if not text:
        return ""

    n = (needle or "").strip()
    if not n:
        return text[: max(0, min(len(text), context * 2 + 20))]

    low = text.lower()
    nlow = n.lower()
    pos = low.find(nlow)
    if pos < 0:
        return text[: max(0, min(len(text), context * 2 + 20))]

    start = max(0, pos - context)
    end = min(len(text), pos + len(n) + context)
    return text[start:end]


def _rect_to_pdf_points_bottom_left(rect: fitz.Rect, page_height: float) -> dict:
    """
    PyMuPDF 座標系：原點在左上，y 向下。
    pdf.js viewport.convertToViewportRectangle 期待：PDF user space（原點在左下，y 向上）。
    因此要做 y 軸翻轉：
      pdf_y0 = H - rect.y1
      pdf_y1 = H - rect.y0
    """
    x0 = float(rect.x0)
    x1 = float(rect.x1)
    y0 = float(page_height - rect.y1)
    y1 = float(page_height - rect.y0)
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


# 檔案清單
@router.get("", response_model=FilesPageOut)
async def list_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    total = db.query(func.count(FileAsset.file_hash)).scalar() or 0

    rows = (
        db.query(FileAsset)
        .order_by(FileAsset.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    wanted_hashes = [r.file_hash for r in rows]
    parsed_map = {h: (EXTRACT_DIR / f"{h}.json").exists()
                  for h in wanted_hashes}

    items = [
        FileAssetOut.model_validate(
            {
                "file_hash": r.file_hash,
                "filename": r.filename,
                "source_url": r.source_url,
                "size_bytes": r.size_bytes,
                "local_path": r.local_path,
                "created_at": r.created_at,
                "parsed": parsed_map.get(r.file_hash, False),
            }
        )
        for r in rows
    ]

    return FilesPageOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/{file_hash}", response_model=FileAssetOut)
def get_file(file_hash: str, db: Session = Depends(get_db)):
    fa = db.get(FileAsset, file_hash)
    if not fa:
        raise HTTPException(404, "file not found")

    parsed = (EXTRACT_DIR / f"{fa.file_hash}.json").exists()
    return FileAssetOut.model_validate(
        {
            "file_hash": fa.file_hash,
            "filename": fa.filename,
            "source_url": fa.source_url,
            "size_bytes": fa.size_bytes,
            "local_path": fa.local_path,
            "created_at": fa.created_at,
            "parsed": parsed,
        }
    )


@router.get("/{file_hash}/search")
def search_in_pdf(
    file_hash: str,
    q: str = Query(..., min_length=1, description="Search term"),
    max_results: int = Query(20, ge=1, le=200),
    context: int = Query(40, ge=0, le=200),
    db: Session = Depends(get_db),
):
    """
    回傳格式（list）：
    [
      {
        "page": 3,
        "snippet": "...",
        "rects": [{"x0":..,"y0":..,"x1":..,"y1":..}, ...],  # PDF point, bottom-left
        "page_size": {"w": 595.0, "h": 842.0},
      },
      ...
    ]
    """
    fa = db.get(FileAsset, file_hash)
    if not fa:
        raise HTTPException(404, "file not found")

    needle = (q or "").strip()
    if not needle:
        raise HTTPException(400, "q cannot be empty")

    pdf_path = Path(fa.local_path)
    if not pdf_path.exists() or not pdf_path.is_file():
        raise HTTPException(404, "file missing on disk")

    results: list[dict] = []

    try:
        with fitz.open(str(pdf_path)) as doc:
            if doc.is_encrypted:
                # 若你未支援密碼解密，這裡直接拒絕（避免回傳空結果造成誤解）
                raise HTTPException(400, "pdf is encrypted")

            for page_index in range(doc.page_count):
                if len(results) >= max_results:
                    break

                page = doc.load_page(page_index)
                page_w = float(page.rect.width)
                page_h = float(page.rect.height)

                # search_for 可能回多筆；跨行也可能回多個 rect（這裡以「每個 rect 當一筆 result」）
                rects = page.search_for(needle)
                if not rects:
                    continue

                # 準備 blocks 供 snippet 擷取（用區塊文字靠近命中位置，避免 text layout 對不齊）
                # (x0,y0,x1,y1, text, block_no, block_type)
                blocks = page.get_text("blocks")

                for r in rects:
                    if len(results) >= max_results:
                        break

                    # 找最貼近的文字 block 當 snippet 來源
                    rr = fitz.Rect(r)
                    probe = rr + (-8, -8, 8, 8)  # 擴一點範圍，跨行更容易抓到文字
                    best_text = ""
                    best_score = -1.0

                    for b in blocks:
                        bx0, by0, bx1, by1, btxt = b[0], b[1], b[2], b[3], b[4]
                        brect = fitz.Rect(bx0, by0, bx1, by1)

                        inter = probe & brect
                        if inter.is_empty:
                            continue

                        # 用交集面積當分數（越大越可能是命中文本所在區塊）
                        score = float(inter.get_area())
                        if score > best_score:
                            best_score = score
                            best_text = btxt or ""

                    snippet = _snippet_from_blocks(best_text, needle, context)

                    # 回傳 rect：轉成 pdf.js 可直接用的 PDF user space（bottom-left）
                    rect_out = _rect_to_pdf_points_bottom_left(rr, page_h)

                    results.append(
                        {
                            "page": page_index + 1,  # 1-based
                            "snippet": snippet,
                            "rects": [rect_out],  # 先一筆；未來你要做 grouping 可改成多筆
                            "page_size": {"w": page_w, "h": page_h},
                        }
                    )

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "failed to read pdf")

    return results


# 多檔上傳
@router.post("/upload-multi")
async def upload_multi(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    items: list[UploadMultiItemOut] = []
    for f in files:
        data = await f.read()
        filename = f.filename or "datasheet.pdf"
        file_hash = await persist_bytes_to_store(db, data, filename, source_url=None)
        items.append(UploadMultiItemOut(
            file_hash=file_hash, filename=filename))
    return UploadMultiOut(uploaded=len(items), items=items)


# 批量 URL 入列（交給 DownloaderWorker）
@router.post("/upload-urls")
async def upload_urls(
    urls: str = Form(...),
    hsd_name: str | None = Form(None),
    db: Session = Depends(get_db),
):
    parsed = [u.strip() for u in (urls or "").splitlines() if u.strip()]
    return await enqueue_urls(urls=parsed, hsd_name=hsd_name, db=db)


# 提供某檔案關聯的型號清單
@router.get("/{file_hash}/models", response_model=List[ModelItemOut])
def list_models_for_file(file_hash: str, db: Session = Depends(get_db)):
    fa = db.get(FileAsset, file_hash)
    if not fa:
        raise HTTPException(404, "file not found")

    models: List[ModelItem] = sorted(
        list(fa.models or []), key=lambda m: (m.model_number or ""))

    def to_out(m: ModelItem) -> ModelItemOut:
        file_lite = [
            FileAssetLite(file_hash=f.file_hash, filename=f.filename) for f in (m.files or [])
        ]
        return ModelItemOut(
            id=m.id,
            model_number=m.model_number,
            input_voltage_range=m.input_voltage_range,
            output_voltage=m.output_voltage,
            output_power=m.output_power,
            package=m.package,
            isolation=m.isolation,
            insulation=m.insulation,
            applications=[t.app_tag for t in (m.applications or [])],
            dimension=m.dimension,
            verify_status=m.verify_status,
            reviewer=m.reviewer,
            reviewed_at=m.reviewed_at,
            notes=m.notes,
            files=file_lite,
        )

    return [to_out(m) for m in models]


# 解除某檔案與某型號的關聯
@router.delete("/{file_hash}/models/{model_number}")
def unlink_model_from_file(file_hash: str, model_number: str, db: Session = Depends(get_db)):
    fa = db.get(FileAsset, file_hash)
    if not fa:
        raise HTTPException(404, "file not found")

    m = db.query(ModelItem).filter_by(model_number=model_number).one_or_none()
    if not m:
        raise HTTPException(404, "model not found")

    link = (
        db.query(FileModelAppearance)
        .filter_by(file_hash=file_hash, model_number=model_number)
        .one_or_none()
    )
    if not link:
        raise HTTPException(404, "link not found")

    db.delete(link)
    db.commit()
    return {"ok": True, "file_hash": file_hash, "model_number": model_number, "unlinked": True}

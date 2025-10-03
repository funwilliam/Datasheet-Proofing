# backend/app/routers/files.py

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from pathlib import Path

from ..db import get_db
from ..models import FileAsset, ModelItem, FileModelAppearance
from ..schemas import FileAssetOut, FileAssetLite, FilesPageOut, UploadMultiItemOut, UploadMultiOut, ModelItemOut
from ..services.file_store import persist_bytes_to_store
from ..routers.downloads import enqueue_urls
from ..settings import settings

router = APIRouter(prefix="/api/files", tags=["files"])

EXTRACT_DIR = settings.WORKSPACE_DIR / "extractions"

# 檔案清單
@router.get("", response_model=FilesPageOut)
async def list_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    # 總筆數（for 分頁）
    total = db.query(func.count(FileAsset.file_hash)).scalar() or 0

    # 分頁查詢
    rows = (
        db.query(FileAsset)
        .order_by(FileAsset.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # 只對這一頁要用到的 hashes 檢查是否已有解析結果
    wanted_hashes = [r.file_hash for r in rows]
    parsed_map = {h: (EXTRACT_DIR / f"{h}.json").exists() for h in wanted_hashes}

    # 組裝 FileAssetOut（注意 parsed 是聚合欄位）
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

    return FileAssetOut.model_validate({
        "file_hash": fa.file_hash,
        "filename": fa.filename,
        "source_url": fa.source_url,
        "size_bytes": fa.size_bytes,
        "local_path": fa.local_path,
        "created_at": fa.created_at,
        "parsed": parsed,
    })

# 多檔上傳
@router.post("/upload-multi")
async def upload_multi(files: List[UploadFile] = File(...), db: Session = Depends(get_db)):
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
async def upload_urls(urls: List[str] = Form(...), hsd_name: str | None = Form(None), db: Session = Depends(get_db)):
    # 這個端點只是為了方便 form-data；真正的任務建立交給 /api/downloads/enqueue 亦可。
    return await enqueue_urls(urls=urls, hsd_name=hsd_name, db=db)  # 重用邏輯

# 提供某檔案關聯的型號清單
@router.get("/{file_hash}/models", response_model=List[ModelItemOut])
def list_models_for_file(file_hash: str, db: Session = Depends(get_db)):
    fa = db.get(FileAsset, file_hash)
    if not fa:
        raise HTTPException(404, "file not found")

    # 透過 relationship（association_proxy）取得關聯的 ModelItem
    models: List[ModelItem] = sorted(list(fa.models or []), key=lambda m: (m.model_number or ""))

    def to_out(m: ModelItem) -> ModelItemOut:
        # files 欄位要給出所有出現過的檔案（不是只有當前這份）
        file_lite = [FileAssetLite(file_hash=f.file_hash, filename=f.filename) for f in (m.files or [])]
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
            reviewed_at=m.reviewed_at,   # 交給 Pydantic 轉 ISO8601
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

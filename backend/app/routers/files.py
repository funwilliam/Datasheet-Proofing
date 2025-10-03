from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ..db import get_db
from ..models import FileAsset, ModelItem, FileModelAppearance
from ..schemas import FileAssetOut, UploadMultiItemOut, UploadMultiOut
from ..services.file_store import persist_bytes_to_store
from ..routers.downloads import enqueue_urls

router = APIRouter(prefix="/api/files", tags=["files"])

@router.get("/", response_model=list[FileAssetOut])
async def list_files(db: Session = Depends(get_db)):
    return db.query(FileAsset).order_by(FileAsset.created_at.desc()).all()

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

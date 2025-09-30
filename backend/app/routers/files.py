from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session
import aiohttp
from typing import List

from ..db import get_db
from ..models import FileAsset, ModelItem
from ..schemas import FileAssetOut, UploadResult
from ..services.file_store import persist_bytes_to_store
from ..routers.downloads import enqueue_urls
# from ..services.pdf_text_index import build_page_index_cached, search_pages
# from ..services.downloader_worker import downloader  # 供前端入列（但批量 URL 用 /api/downloads/enqueue）

router = APIRouter(prefix="/api/files", tags=["files"])

@router.get("/", response_model=list[FileAssetOut])
async def list_files(db: Session = Depends(get_db)):
    return db.query(FileAsset).order_by(FileAsset.created_at.desc()).all()

# # ── 既有單檔或單一 URL（保留）
# @router.post("/upload", response_model=UploadResult)
# async def upload_file(
#     file: UploadFile = File(None),
#     source_url: str | None = Form(None),
#     hsd_name: str | None = Form(None),
#     db: Session = Depends(get_db),
# ):
#     if file is None and not source_url:
#         raise HTTPException(400, "file or source_url required")

#     if file is not None:
#         data = await file.read()
#         filename = file.filename or "datasheet.pdf"
#         file_hash = await persist_bytes_to_store(db, data, filename, source_url=None)
#         file_exists = True
#         has_parsed = db.query(ModelItem).filter(ModelItem.file_hash==file_hash).first() is not None
#         return UploadResult(file_hash=file_hash, file_exists=file_exists, has_parsed_models=bool(has_parsed))

#     async with aiohttp.ClientSession() as session:
#         async with session.get(source_url) as resp:
#             if resp.status != 200:
#                 raise HTTPException(422, f"download failed status={resp.status}")
#             content = await resp.read()
#             if not content:
#                 raise HTTPException(422, "downloaded empty content")
#             filename = source_url.split("/")[-1] or "datasheet.pdf"
#             file_hash = await persist_bytes_to_store(db, content, filename, source_url=source_url)
#             file_exists = True
#             has_parsed = db.query(ModelItem).filter(ModelItem.file_hash==file_hash).first() is not None
#             return UploadResult(file_hash=file_hash, file_exists=file_exists, has_parsed_models=bool(has_parsed))

# ── 新增：多檔上傳
@router.post("/upload-multi")
async def upload_multi(files: List[UploadFile] = File(...), db: Session = Depends(get_db)):
    results = []
    for f in files:
        data = await f.read()
        filename = f.filename or "datasheet.pdf"
        file_hash = await persist_bytes_to_store(db, data, filename, source_url=None)
        has_parsed = db.query(ModelItem).filter(ModelItem.file_hash==file_hash).first() is not None
        results.append({"file_hash": file_hash, "filename": filename, "has_parsed_models": bool(has_parsed)})
    return {"uploaded": len(results), "items": results}

# ── 新增：批量 URL 入列（交給 DownloaderWorker）
@router.post("/upload-urls")
async def upload_urls(urls: List[str] = Form(...), hsd_name: str | None = Form(None), db: Session = Depends(get_db)):
    # 這個端點只是為了方便 form-data；真正的任務建立交給 /api/downloads/enqueue 亦可。
    
    return await enqueue_urls(urls=urls, hsd_name=hsd_name, db=db)  # 重用邏輯

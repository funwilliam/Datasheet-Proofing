# backend/app/routers/tasks.py

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List

from ..db import get_db
from ..models import ExtractionTask, DownloadTask, FileAsset
from ..services.extractor_worker import extractor_worker
from ..schemas import QueueRequest
from ..settings import settings

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

EXTRACT_DIR = settings.WORKSPACE_DIR / "extractions"

def _has_extraction_json(file_hash: str) -> bool:
    p = EXTRACT_DIR / f"{file_hash}.json"
    try:
        return p.exists() and p.is_file()
    except Exception:
        return False

# ── 入列（向下相容：單/多檔擷取；跳過已存在 JSON 且非 force_rerun）
@router.post("/queue")
async def queue_extract(req: QueueRequest, db: Session = Depends(get_db)):
    if not req.file_hashes:
        raise HTTPException(status_code=400, detail="file_hashes cannot be empty")

    # 去重，並記錄重複
    seen = set()
    deduped: List[str] = []
    duplicates_ignored: List[str] = []
    for h in req.file_hashes:
        h = (h or "").strip()
        if not h:
            continue
        if h in seen:
            duplicates_ignored.append(h)
            continue
        seen.add(h)
        deduped.append(h)

    if not deduped:
        return {
            "queued": 0,
            "skipped_existing": 0,
            "not_found": 0,
            "duplicates_ignored": len(duplicates_ignored),
            "total_input": len(req.file_hashes),
            "queued_hashes": [],
            "skipped_hashes": [],
            "not_found_hashes": [],
        }

    queued_hashes: List[str] = []
    skipped_hashes: List[str] = []
    not_found_hashes: List[str] = []

    for h in deduped:
        fa = db.get(FileAsset, h)
        if not fa:
            not_found_hashes.append(h)
            continue

        if (not req.force_rerun) and _has_extraction_json(h):
            # 已有結果且不強制重跑 → 跳過
            skipped_hashes.append(h)
            continue

        await extractor_worker.enqueue(h, req.force_rerun)
        queued_hashes.append(h)

    return {
        "queued": len(queued_hashes),
        "skipped_existing": len(skipped_hashes),
        "not_found": len(not_found_hashes),
        "duplicates_ignored": len(duplicates_ignored),
        "total_input": len(req.file_hashes),
        "queued_hashes": queued_hashes,
        "skipped_hashes": skipped_hashes,
        "not_found_hashes": not_found_hashes,
    }

# ── ExtractionTask 列表
@router.get("/extraction")
def list_extraction_tasks(
    db: Session = Depends(get_db),
    limit: int = Query(200, ge=1, le=1000),
    status: Optional[str] = Query(None, description="queued/submitted/running/succeeded/failed/canceled"),
    mode: Optional[str] = Query(None, description="sync/batch/background"),
):
    q = db.query(ExtractionTask)
    if status:
        q = q.filter(ExtractionTask.status == status)
    if mode:
        q = q.filter(ExtractionTask.mode == mode)
    rows = q.order_by(ExtractionTask.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "mode": r.mode,
            "status": r.status,
            "file_hash": r.file_hash,
            "file_hashes": r.file_hashes,
            "openai_model": r.openai_model,
            "service_tier": r.service_tier,
            "external_ids": r.external_ids,
            "cost_usd": r.cost_usd,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "input_tokens": r.input_tokens,
            "cached_input_tokens": r.cached_input_tokens,
            "output_tokens": r.output_tokens,
            "request_payload_path": r.request_payload_path,
            "response_path": r.response_path,
            "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in rows
    ]

# ── DownloadTask 列表
@router.get("/download")
def list_download_tasks(
    db: Session = Depends(get_db),
    limit: int = Query(200, ge=1, le=1000),
    status: Optional[str] = Query(None, description="queued/running/success/failed"),
):
    q = db.query(DownloadTask)
    if status:
        q = q.filter(DownloadTask.status == status)
    rows = q.order_by(DownloadTask.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "source_url": r.source_url,
            "hsd_name": r.hsd_name,
            "status": r.status,
            "file_hash": r.file_hash,
            "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in rows
    ]

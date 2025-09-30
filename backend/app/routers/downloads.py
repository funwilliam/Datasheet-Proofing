from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import Optional, List

from ..db import get_db
from ..models import DownloadTask
from ..services.downloader_worker import downloader_worker

router = APIRouter(prefix="/api/downloads", tags=["downloads"])

@router.post("/enqueue")
async def enqueue_urls(urls: List[str], hsd_name: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Body: { "urls": ["https://...", "https://..."], "hsd_name": "Mouser" }
    """
    if not urls:
        raise HTTPException(400, "urls cannot be empty")

    created_ids: list[int] = []
    now = datetime.now(timezone.utc)
    for u in urls:
        t = DownloadTask(
            source_url=u.strip(),
            hsd_name=hsd_name,
            status="queued",
            created_at=now,
        )
        db.add(t)
        db.commit()
        created_ids.append(t.id)
        await downloader_worker.enqueue(t.id)

    return {"queued": len(created_ids), "task_ids": created_ids}

@router.get("/")
def list_downloads(
    db: Session = Depends(get_db),
    limit: int = Query(200, ge=1, le=1000),
    status: Optional[str] = Query(None, description="Filter: queued/running/success/failed"),
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

@router.post("/{task_id}/retry")
async def retry_download(task_id: int, db: Session = Depends(get_db)):
    t = db.get(DownloadTask, task_id)
    if not t:
        raise HTTPException(404, "not found")
    t.status = "queued"
    t.error = None
    t.started_at = None
    t.completed_at = None
    db.commit()
    await downloader_worker.enqueue(t.id)
    return {"ok": True}

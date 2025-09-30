import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from ..db import get_db
from ..settings import settings
from ..models import ExtractionTask, ModelItem
from ..schemas import ExtractionTaskOut

router = APIRouter(prefix="/api/extractions", tags=["extractions"])

@router.get("/{file_hash}")
async def get_extraction_summary(file_hash: str, db: Session = Depends(get_db)):
    ex = db.query(ExtractionTask).filter(ExtractionTask.file_hash==file_hash).order_by(ExtractionTask.id.desc()).first()
    if not ex:
        raise HTTPException(404, "not found")
    items = db.query(ModelItem).filter(ModelItem.file_hash==file_hash).all()
    return {
        "extraction": ExtractionTaskOut.model_validate(ex).model_dump(),
        "models": [{
            "id": i.id,
            "file_hash": i.file_hash,
            "model_number": i.model_number,
            "fields_json": json.loads(i.fields_json),
            "verify_status": i.verify_status,
            "reviewer": i.reviewer,
            "reviewed_at": i.reviewed_at,
            "notes": i.notes,
        } for i in items]
    }

@router.get("/{task_id}/output")
async def download_extraction_output(task_id: int, db: Session = Depends(get_db)):
    """用 task_id 下載擷取結果 JSON。"""
    t = db.get(ExtractionTask, task_id)
    if not t or not t.response_path:
        raise HTTPException(404, "not found")
    p = Path(t.response_path)

    # 安全檢查：必須在工作區的 'extractions' 資料夾底下
    extra_dir = settings.WORKSPACE_DIR / "extractions"
    try:
        p = p.resolve()
        if extra_dir.resolve() not in p.parents and p != extra_dir:
            raise HTTPException(403, "invalid path")
        if not p.exists():
            raise HTTPException(404, "file missing on disk")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "bad path")

    return FileResponse(
        path=str(p),
        media_type="application/json",
        filename=p.name
    )
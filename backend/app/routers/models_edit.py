from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import json
from pathlib import Path

from ..db import get_db
from ..models import ModelItem
from ..schemas import ModelVerifyUpdate

router = APIRouter(prefix="/api/models", tags=["models"])

def _load_item_schema():
    schema_path = Path(__file__).resolve().parents[3] / "resources" / "openai" / "response_format" / "擷取規格.json"
    try:
        data = json.loads(schema_path.read_text(encoding="utf-8"))
        return data["properties"]["models"]["items"]
    except Exception:
        return None

@router.get("/{model_id}")
async def get_model(model_id: int, db: Session = Depends(get_db)):
    m = db.get(ModelItem, model_id)
    if not m:
        raise HTTPException(404, "not found")
    return {
        "id": m.id,
        "file_hash": m.file_hash,
        "model_number": m.model_number,
        "fields_json": json.loads(m.fields_json),
        "verify_status": m.verify_status,
        "reviewer": m.reviewer,
        "reviewed_at": m.reviewed_at,
        "notes": m.notes,
    }

@router.patch("/{model_id}")
async def update_model(model_id: int, body: ModelVerifyUpdate, db: Session = Depends(get_db)):
    m = db.get(ModelItem, model_id)
    if not m:
        raise HTTPException(404, "not found")

    if body.fields_json is not None:
        # optional schema validation (only if schema file exists)
        item_schema = _load_item_schema()
        if item_schema is not None:
            try:
                from jsonschema import Draft202012Validator
                Draft202012Validator(item_schema).validate(body.fields_json)
            except Exception as e:
                raise HTTPException(422, f"schema validation failed: {e}")
        m.fields_json = json.dumps(body.fields_json, ensure_ascii=False)

    if body.verify_status is not None:
        if body.verify_status not in ("pending","accepted","corrected","rejected"):
            raise HTTPException(400, "invalid status")
        m.verify_status = body.verify_status

    if body.reviewer is not None:
        m.reviewer = body.reviewer
    if body.notes is not None:
        m.notes = body.notes

    m.reviewed_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    return {"ok": True}

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
import json
from ..db import get_db
from ..models import ModelItem
from ..settings import settings
from pathlib import Path

router = APIRouter(prefix="/api/export", tags=["export"])

@router.get("/")
async def export_data(status: str = "accepted", fmt: str = "json", db: Session = Depends(get_db)):
    q = db.query(ModelItem)
    if status:
        q = q.filter(ModelItem.verify_status==status)
    rows = q.order_by(ModelItem.file_hash.asc(), ModelItem.model_number.asc()).all()

    if fmt == "json":
        payload = [json.loads(r.fields_json) for r in rows]
        return payload
    elif fmt == "csv":
        import csv
        out_path = settings.WORKSPACE_DIR / "exports" / "export.csv"
        headers = [
            "Model Number",
            "Input Voltage.lower",
            "Input Voltage.upper",
            "Input Voltage.nominal",
            "Output Voltage.value",
            "Output Current.value",
            "Output Power.value",
            "Efficiency.value",
        ]
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(headers)
            for r in rows:
                obj = json.loads(r.fields_json)
                def gx(path):
                    try:
                        cur = obj
                        for k in path.split('.'):
                            cur = cur[k]
                        return cur
                    except Exception:
                        return ""
                w.writerow([
                    obj.get("Model Number",""),
                    gx("Input Voltage.lower"), gx("Input Voltage.upper"), gx("Input Voltage.nominal"),
                    gx("Output Voltage.value"), gx("Output Current.value"), gx("Output Power.value"), gx("Efficiency.value"),
                ])
        return Response(
            content=out_path.read_bytes(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="export.csv"'}
        )
    else:
        return {"error": "unsupported fmt"}

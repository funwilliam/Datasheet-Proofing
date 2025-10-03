# backend/app/routers/export.py

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Iterable, Literal
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import or_, case

import io
import csv

from ..db import get_db
from ..models import ModelItem

router = APIRouter(prefix="/api/export", tags=["export"])


# ──────────────────────────────────────────────────────────────────────────────
# 共用工具
# ──────────────────────────────────────────────────────────────────────────────

def _dt_to_iso_z(dt: Optional[datetime]) -> Optional[str]:
    """將 datetime 轉成 ISO8601（UTC、結尾 Z）。None 則回 None。"""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _serialize_model_to_json(m: ModelItem) -> Dict[str, Any]:
    """JSON 匯出用：與 /api/models/{model_number} 類似，但偏批次匯出格式。"""
    apps = [t.app_tag for t in (m.applications or [])]

    # files 依建立時間新到舊；若無 created_at 就原序
    files_out: List[Dict[str, Any]] = []
    try:
        files_sorted = sorted(list(m.files or []), key=lambda fa: (fa.created_at or 0), reverse=True)
    except Exception:
        files_sorted = list(m.files or [])

    for fa in files_sorted:
        files_out.append({"file_hash": fa.file_hash, "filename": fa.filename})

    return {
        "model_number": m.model_number,
        "input_voltage_range": m.input_voltage_range,
        "output_voltage": m.output_voltage,
        "output_power": m.output_power,
        "package": m.package,
        "isolation": m.isolation,
        "insulation": m.insulation,
        "dimension": m.dimension,
        "applications": apps,
        "verify_status": m.verify_status,
        "reviewer": m.reviewer,
        "reviewed_at": _dt_to_iso_z(m.reviewed_at),
        "files": files_out,
    }


def _serialize_model_to_csv_row(m: ModelItem) -> Dict[str, str]:
    """CSV 匯出用：攤平欄位；applications 與 files 以 '; ' 連接。"""
    apps = [t.app_tag for t in (m.applications or [])]

    try:
        files_sorted = sorted(list(m.files or []), key=lambda fa: (fa.created_at or 0), reverse=True)
    except Exception:
        files_sorted = list(m.files or [])
    file_names = [fa.filename or "" for fa in files_sorted]

    return {
        "model_number": m.model_number or "",
        "input_voltage_range": m.input_voltage_range or "",
        "output_voltage": m.output_voltage or "",
        "output_power": m.output_power or "",
        "package": m.package or "",
        "isolation": m.isolation or "",
        "insulation": m.insulation or "",
        "dimension": m.dimension or "",
        "applications": "; ".join(apps),
        "verify_status": (m.verify_status or ""),
        "reviewer": (m.reviewer or ""),
        "reviewed_at": (_dt_to_iso_z(m.reviewed_at) or ""),
        "files": "; ".join(file_names),
    }


def _chunked_in_filter(q, column, values: List[str], chunk_size: int = 900):
    """
    為了避開 SQLite 預設 999 參數上限，將 IN 子句拆塊，用 OR 串接。
    回傳加上過濾條件後的 query。
    """
    cleaned = [v for v in (values or []) if isinstance(v, str) and v.strip()]
    if not cleaned:
        # 空清單 → 無資料
        return q.filter(False)  # 直接傳回空結果
    ors = []
    for i in range(0, len(cleaned), chunk_size):
        chunk = cleaned[i: i + chunk_size]
        ors.append(column.in_(chunk))
    return q.filter(or_(*ors))


def _unique_in_order(seq: Iterable[str]) -> List[str]:
    """去重但保留第一次出現的順序；自動去掉空字串/None。"""
    seen = set()
    out: List[str] = []
    for x in seq or []:
        if not isinstance(x, str):
            continue
        s = x.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _csv_stream(rows: Iterable[ModelItem]) -> Iterable[bytes]:
    """
    以串流方式產出 CSV bytes，避免一次載入記憶體。
    """
    fieldnames = [
        "model_number",
        "input_voltage_range",
        "output_voltage",
        "output_power",
        "package",
        "isolation",
        "insulation",
        "dimension",
        "applications",
        "verify_status",
        "reviewer",
        "reviewed_at",
        "files",
    ]
    # 寫 header
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    yield buf.getvalue().encode("utf-8-sig")  # BOM + header
    buf.seek(0)
    buf.truncate(0)

    # 寫 rows
    for m in rows:
        writer.writerow(_serialize_model_to_csv_row(m))
        yield buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate(0)


# ──────────────────────────────────────────────────────────────────────────────
# 既有：整庫匯出
# ──────────────────────────────────────────────────────────────────────────────

@router.get("")
def export_data(
    status: Optional[str] = None,   # 例：'verified' 或 'unverified'；None/空字串則不過濾
    fmt: str = "json",              # 'json' | 'csv'
    db: Session = Depends(get_db),
):
    """
    匯出 ModelItem 資料（全庫）。
    - /api/export?fmt=json
    - /api/export?fmt=csv
    - /api/export?status=verified&fmt=csv  僅匯出 verify_status = 'verified' 的資料
    """
    q = db.query(ModelItem)
    if status:
        q = q.filter(ModelItem.verify_status == status)

    # 穩定排序：model_number
    q = q.order_by(ModelItem.model_number.asc())
    rows: List[ModelItem] = q.all()

    if fmt.lower() == "json":
        return [_serialize_model_to_json(m) for m in rows]

    if fmt.lower() == "csv":
        headers = {
            "Content-Disposition": 'attachment; filename="models_export.csv"',
            "Cache-Control": "no-store",
        }
        return StreamingResponse(_csv_stream(rows), media_type="text/csv; charset=utf-8", headers=headers)

    raise HTTPException(status_code=400, detail="unsupported fmt (use 'json' or 'csv')")


# ──────────────────────────────────────────────────────────────────────────────
# 新增：指定型號清單匯出（長清單 OK）
# ──────────────────────────────────────────────────────────────────────────────

class ExportByModelsIn(BaseModel):
    model_numbers: List[str] = Field(..., description="要匯出的型號清單")
    status: Optional[str] = Field(None, description="過濾 verify_status（例：verified/unverified）；None 表示不過濾")
    fmt: Literal["json", "csv"] = Field("json", description="輸出格式")
    preserve_order: bool = Field(False, description="是否依照輸入清單的順序輸出")


@router.post("/by-models")
def export_by_models(payload: ExportByModelsIn, db: Session = Depends(get_db)):
    """
    指定型號清單匯出：
      POST /api/export/by-models
      Body:
      {
        "model_numbers": ["ABC-123", "XYZ-999", ...],   # 可非常長
        "status": "verified",    # 可省略
        "fmt": "json" | "csv",
        "preserve_order": true   # 依照 model_numbers 傳入順序輸出
      }
    """
    # 先清洗 + 去重（保留順序）
    model_numbers = _unique_in_order(payload.model_numbers)
    if not model_numbers:
        # 空清單
        if payload.fmt.lower() == "json":
            return []
        return Response(content=b"", media_type="text/csv")

    q = db.query(ModelItem)

    # 依型號清單分塊過濾
    q = _chunked_in_filter(q, ModelItem.model_number, model_numbers)

    # 可選狀態過濾
    if payload.status:
        q = q.filter(ModelItem.verify_status == payload.status)

    # 排序策略
    if payload.preserve_order:
        # 關鍵：用 case((條件, 值), ...)，不要把 dict 當 SQL 參數
        order_case = case(
            *[(ModelItem.model_number == mn, idx) for idx, mn in enumerate(model_numbers)],
            else_=len(model_numbers),
        )
        q = q.order_by(order_case)
    else:
        q = q.order_by(ModelItem.model_number.asc())

    rows: List[ModelItem] = q.all()

    # 為了不同 DB 的細微差異，即使已用 SQL 排序，JSON 再保險一次
    if payload.preserve_order and payload.fmt.lower() == "json":
        idx = {mn: i for i, mn in enumerate(model_numbers)}
        rows.sort(key=lambda m: idx.get(m.model_number, len(idx) + 1))

    if payload.fmt.lower() == "json":
        return [_serialize_model_to_json(m) for m in rows]

    if payload.fmt.lower() == "csv":
        headers = {
            "Content-Disposition": 'attachment; filename="models_export_selected.csv"',
            "Cache-Control": "no-store",
        }
        return StreamingResponse(_csv_stream(rows), media_type="text/csv; charset=utf-8", headers=headers)

    raise HTTPException(status_code=400, detail="unsupported fmt (use 'json' or 'csv')")

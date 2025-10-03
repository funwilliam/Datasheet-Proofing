# backend/app/routers/models.py

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from ..db import get_db
from ..models import ModelItem, ModelApplicationTag
from ..schemas import ModelUpsertIn, ModelItemOut, ModelItemLiteOut, ModelsPageOut, FileAssetLite

router = APIRouter(prefix="/api/models", tags=["models"])

# ─────────────────────────────
# 工具：正規化（空字串→None）
def _norm(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None

def _apps_to_list(mi: ModelItem) -> List[str]:
    return [t.app_tag for t in (mi.applications or [])]

# ─────────────────────────────
# 取得型號清單（分頁）
@router.get("", response_model=ModelsPageOut)
def list_models(
    q: Optional[str] = None,                     # 只比對 model_number
    status: Optional[str] = None,                # verified/unverified
    has_files: Optional[bool] = None,            # 是否有關聯檔案
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ModelsPageOut:
    base = db.query(ModelItem)

    if q:
        like = f"%{q.strip()}%"
        base = base.filter(ModelItem.model_number.ilike(like))

    if status in ("verified", "unverified"):
        base = base.filter(ModelItem.verify_status == status)

    if has_files is not None:
        if has_files:
            base = base.filter(ModelItem.files.any())
        else:
            base = base.filter(~ModelItem.files.any())

    # ---- 總數（distinct 防 join 重複）
    total = (
        base.with_entities(ModelItem.id)
            .distinct()
            .count()
    )

    # ---- 分頁資料
    rows = (
        base.distinct(ModelItem.id)
            .order_by(ModelItem.model_number.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
    )

    items: List[ModelItemLiteOut] = []
    for m in rows:
        files = [FileAssetLite(file_hash=fa.file_hash, filename=fa.filename) for fa in (m.files or [])]
        items.append(
            ModelItemLiteOut(
                model_number=m.model_number,
                verify_status=m.verify_status,
                reviewer=m.reviewer,
                reviewed_at=m.reviewed_at,  # 讓 Pydantic 自動轉 ISO8601
                files=files,
            )
        )

    return ModelsPageOut(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )

# ─────────────────────────────
# 取得單一型號（以 model_number）
@router.get("/{model_number}", response_model=ModelItemOut)
def get_model(model_number: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    m = db.query(ModelItem).filter_by(model_number=model_number).one_or_none()
    if not m:
        raise HTTPException(404, "model not found")

    # 把出現的檔案也回傳（用 association_proxy: m.files）
    try:
        files = sorted(list(m.files or []), key=lambda fa: (fa.created_at or 0), reverse=True)
    except Exception:
        files = list(m.files or [])

    return {
        "id": m.id,
        "model_number": m.model_number,
        "input_voltage_range": m.input_voltage_range,
        "output_voltage": m.output_voltage,
        "output_power": m.output_power,
        "package": m.package,
        "isolation": m.isolation,
        "insulation": m.insulation,
        "applications": _apps_to_list(m),
        "dimension": m.dimension,
        "verify_status": m.verify_status,
        "reviewer": m.reviewer,
        "reviewed_at": m.reviewed_at,  # FastAPI 會自動轉 ISO8601
        "notes": m.notes,
        "files": [
            {"file_hash": fa.file_hash, "filename": fa.filename}
            for fa in files
        ],
    }

@router.patch("/{model_number}")
def update_model(model_number: str, body: ModelUpsertIn, db: Session = Depends(get_db)):
    m = db.query(ModelItem).filter_by(model_number=model_number).one_or_none()
    if not m:
        raise HTTPException(404, "model not found")

    changed = False

    # 欄位更新（空字串視為 None）
    for col in ["input_voltage_range", "output_voltage", "output_power",
                "package", "isolation", "insulation", "dimension", "notes"]:
        if getattr(body, col) is not None:
            new_val = _norm(getattr(body, col))
            old_val = _norm(getattr(m, col))
            if new_val != old_val:
                setattr(m, col, new_val)
                changed = True

    # applications 全量替換
    if body.applications is not None:
        new_tags_canon = {(t or "").strip().lower() for t in body.applications if (t or "").strip()}
        old_map = {t.app_tag_canon: t for t in (m.applications or [])}
        old_set = set(old_map.keys())

        # 刪除不存在的
        for canon in list(old_set - new_tags_canon):
            db.delete(old_map[canon])
            changed = True

        # 新增新的
        for canon in list(new_tags_canon - old_set):
            original = next((t for t in body.applications if (t or "").strip().lower() == canon), None)
            if original:
                db.add(ModelApplicationTag(model=m, app_tag=original.strip(), app_tag_canon=canon))
                changed = True

    # verify_status 處理
    if body.verify_status is not None:
        if body.verify_status not in ("unverified", "verified"):
            raise HTTPException(400, "invalid verify_status")
        m.verify_status = body.verify_status
        if body.verify_status == "verified":
            if body.reviewer is not None:
                m.reviewer = _norm(body.reviewer)
            m.reviewed_at = datetime.now(timezone.utc)
        else:
            m.reviewer = None
            m.reviewed_at = None
        changed = True
    else:
        if changed and m.verify_status == "verified":
            m.verify_status = "unverified"
            m.reviewer = None
            m.reviewed_at = None

    if changed:
        db.commit()

    return get_model(model_number, db)

# ─────────────────────────────
# 刪除整個型號（連帶刪除 applications 與 file 連結，靠外鍵 cascade）
@router.delete("/{model_number}")
def delete_model(model_number: str, db: Session = Depends(get_db)):
    m = db.query(ModelItem).filter_by(model_number=model_number).one_or_none()
    if not m:
        raise HTTPException(404, "model not found")
    db.delete(m)
    db.commit()
    return {"ok": True, "deleted": model_number}

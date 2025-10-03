# backend/app/routers/static_proxy.py

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pathlib import Path
from ..settings import settings

router = APIRouter(prefix="/api/static", tags=["static"])

# 只允許工作區某幾個子資料夾
ALLOWED_BASES = [
    settings.WORKSPACE_DIR / "extractions",
    settings.WORKSPACE_DIR / "exports",
]

@router.get("")
def serve_path(path: str = Query(..., description="Absolute or workspace-relative path")):
    p = Path(path)
    if not p.is_absolute():
        p = (settings.ROOT / p).resolve()

    # 白名單校驗
    try:
        rp = p.resolve(strict=True)
    except Exception:
        raise HTTPException(404, "file not found")

    ok = any(str(rp).startswith(str(base.resolve())) for base in ALLOWED_BASES)
    if not ok:
        raise HTTPException(403, "forbidden")

    return FileResponse(str(rp), media_type="application/json")

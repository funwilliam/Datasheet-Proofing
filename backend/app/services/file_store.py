from __future__ import annotations
from typing import Optional
from pathlib import Path
import hashlib
import io
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from ..settings import settings
from ..models import FileAsset

STORE_DIR = settings.WORKSPACE_DIR / "store"

class HashableBytesIO(io.BytesIO):
    name: Optional[str] = None
    @property
    def hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.getvalue())
        return h.hexdigest()

async def persist_bytes_to_store(db: Session, data: bytes, filename: str, source_url: Optional[str]) -> str:
    hb = HashableBytesIO(data)
    hb.name = filename or "datasheet.pdf"
    file_hash = hb.hash
    pdf_path = STORE_DIR / f"{file_hash}.pdf"
    if not pdf_path.exists():
        pdf_path.write_bytes(data)
    # upsert
    fa = db.get(FileAsset, file_hash)
    if not fa:
        fa = FileAsset(
            file_hash=file_hash,
            filename=filename,
            source_url=source_url,
            size_bytes=len(data),
            local_path=str(pdf_path),
            created_at=datetime.now(timezone.utc),
        )
        db.add(fa)
        db.commit()
    return file_hash

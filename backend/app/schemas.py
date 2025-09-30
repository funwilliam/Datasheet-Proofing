from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List, Any, Dict

# ── FileAsset
class FileAssetOut(BaseModel):
    file_hash: str
    filename: str
    source_url: Optional[str]
    size_bytes: Optional[int]
    local_path: str
    created_at: datetime
    class Config:
        from_attributes = True

class UploadResult(BaseModel):
    file_hash: str
    file_exists: bool
    has_parsed_models: bool

# ── Queue request (維持相容)
class QueueRequest(BaseModel):
    file_hashes: List[str]
    force_rerun: bool = False

# ── DownloadTask
class DownloadTaskOut(BaseModel):
    id: int
    source_url: str
    hsd_name: Optional[str] = None
    status: str
    file_hash: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    class Config:
        from_attributes = True

# ── ExtractionTask（新）
class ExtractionTaskOut(BaseModel):
    id: int
    mode: str                       # sync/batch/background
    status: str                     # queued/submitted/running/succeeded/failed/canceled
    provider: Optional[str] = None
    openai_model: Optional[str] = None
    service_tier: Optional[str] = None
    file_hash: Optional[str] = None
    file_hashes: Optional[List[str]] = None
    external_ids: Optional[Dict[str, Any]] = None
    vector_store_id: Optional[str] = None

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    input_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None

    request_payload_path: Optional[str] = None
    response_path: Optional[str] = None
    error: Optional[str] = None
    retry_count: Optional[int] = None
    schema_version: Optional[str] = None

    created_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# ── ModelItem
class ModelItemOut(BaseModel):
    id: int
    file_hash: str
    model_number: str
    fields_json: Any
    verify_status: str
    reviewer: Optional[str]
    reviewed_at: Optional[datetime]
    notes: Optional[str]
    class Config:
        from_attributes = True

class ModelVerifyUpdate(BaseModel):
    fields_json: Optional[Any] = None
    verify_status: Optional[str] = None
    reviewer: Optional[str] = None
    notes: Optional[str] = None

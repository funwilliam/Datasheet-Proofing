# backend/app/schemas.py
from datetime import datetime
from pydantic import BaseModel, Field
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

# ── Upload multi
class UploadMultiItemOut(BaseModel):
    file_hash: str
    filename: str
    
class UploadMultiOut(BaseModel):
    uploaded: int
    items: List[UploadMultiItemOut]


# ── Queue request
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

# ── ExtractionTask
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
    model_number: str

    # 規格
    input_voltage_range: Optional[str] = None
    output_voltage: Optional[str] = None
    output_power: Optional[str] = None
    package: Optional[str] = None
    isolation: Optional[str] = None
    insulation: Optional[str] = None
    applications: List[str] = Field(default_factory=list)
    dimension: Optional[str] = None

    verify_status: str              # 'unverified' / 'verified'
    reviewer: Optional[str]
    reviewed_at: Optional[datetime]
    notes: Optional[str]

    class Config:
        from_attributes = True

class ModelUpsertIn(BaseModel):
    input_voltage_range: Optional[str] = None
    output_voltage: Optional[str] = None
    output_power: Optional[str] = None
    package: Optional[str] = None
    isolation: Optional[str] = None
    insulation: Optional[str] = None
    applications: Optional[List[str]] = None
    dimension: Optional[str] = None
    
    verify_status: Optional[str] = None      # 'unverified' / 'verified'
    reviewer: Optional[str] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True

# backend/app/models.py
from sqlalchemy import (
    Column, Integer, String, Text, ForeignKey, CheckConstraint, UniqueConstraint,
    Float, JSON, Index
)
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship
from .db import Base
from .db_types import AwareDateTime # 自訂義的 Datetime 修飾器

class FileAsset(Base):
    __tablename__ = "file_asset"
    file_hash   = Column(String, primary_key=True)  # SHA-256
    filename    = Column(String, nullable=False)
    source_url  = Column(Text, nullable=True)
    size_bytes  = Column(Integer, nullable=True)
    local_path  = Column(Text, nullable=False)
    created_at  = Column(AwareDateTime, nullable=False)
    
    # 關聯物件（1 ↔ N）
    appearances = relationship(
        "FileModelAppearance",
        back_populates="file",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    # 捷徑：直接拿到此檔案的所有 ModelItem
    models = association_proxy("appearances", "model", creator=lambda m: FileModelAppearance(model=m))

class ModelApplicationTag(Base):
    __tablename__ = "model_application_tag"
    id              = Column(Integer, primary_key=True)
    model_number    = Column(String, ForeignKey("model_item.model_number", ondelete="CASCADE"), nullable=False, index=True)

    # 純字串 tag；建議儲存一個「規範化小寫」欄位便於查詢比對
    app_tag         = Column(String, nullable=False)      # 原始輸入（保留大小寫/空白）
    app_tag_canon   = Column(String, nullable=False)      # 規範化（trim + lower）

    __table_args__ = (
        UniqueConstraint("model_number", "app_tag_canon", name="uq_model_app_once"),
        Index("ix_app_model", "app_tag_canon", "model_number"),     # 反查用
    )

    model = relationship("ModelItem", back_populates="applications")

class ModelItem(Base):
    __tablename__ = "model_item"
    id                      = Column(Integer, primary_key=True)
    model_number            = Column(String, nullable=False, unique=True, index=True)

    input_voltage_range     = Column(String, nullable=True)
    output_voltage          = Column(String, nullable=True)
    output_power            = Column(String, nullable=True)
    package                 = Column(String, nullable=True)
    isolation               = Column(String, nullable=True)
    insulation              = Column(String, nullable=True)
    dimension               = Column(String, nullable=True)

    verify_status           = Column(String, nullable=False, default="unverified")
    reviewer                = Column(String, nullable=True)
    reviewed_at             = Column(AwareDateTime, nullable=True)
    notes                   = Column(Text, nullable=True)

    # N ↔ 1 關聯物件
    appearances = relationship(
        "FileModelAppearance",
        back_populates="model",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    # 捷徑：直接拿到含有此型號的所有 FileAsset
    files = association_proxy("appearances", "file", creator=lambda f: FileModelAppearance(file=f))

    applications = relationship(
        "ModelApplicationTag",
        back_populates="model",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint("verify_status in ('unverified','verified')", name="ck_verify_status"),
    )


class FileModelAppearance(Base):
    __tablename__ = "file_model_appearance"
    id           = Column(Integer, primary_key=True)
    file_hash    = Column(String, ForeignKey("file_asset.file_hash", ondelete="CASCADE"), nullable=False, index=True)
    model_number = Column(String, ForeignKey("model_item.model_number", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("file_hash", "model_number", name="uq_file_model_once"),
    )

    file  = relationship("FileAsset", back_populates="appearances", passive_deletes=True)
    model = relationship("ModelItem", back_populates="appearances", passive_deletes=True)

# ─────────────────────────────────────────────────────────────────────────────
# 下載任務（task）
class DownloadTask(Base):
    __tablename__ = "download_task"
    id              = Column(Integer, primary_key=True)
    source_url      = Column(Text, nullable=False)
    hsd_name        = Column(String, nullable=True)
    status          = Column(String, nullable=False, default="queued")  # queued/running/success/failed
    file_hash       = Column(String, nullable=True)
    error           = Column(Text, nullable=True)

    created_at      = Column(AwareDateTime, nullable=False)
    started_at      = Column(AwareDateTime, nullable=True)
    completed_at    = Column(AwareDateTime, nullable=True)

# ─────────────────────────────────────────────────────────────────────────────
# OpenAI 擷取任務（task）
class ExtractionTask(Base):
    __tablename__ = "extraction_task"
    id                      = Column(Integer, primary_key=True)

    # 資料範圍：舊流程是一個 file_hash；batch/background 可能多檔
    file_hash               = Column(String, nullable=True)       # 單檔時填
    file_hashes             = Column(JSON, nullable=True)       # 多檔時填 List[str]

    # 模式與外部追蹤
    mode                    = Column(String, nullable=False, default="sync")  # sync/batch/background
    provider                = Column(String, nullable=False, default="openai")  # 保留擴充 (openai/azure-openai/…)
    openai_model            = Column(String, nullable=True)    # e.g. gpt-5
    service_tier            = Column(String, nullable=True)    # e.g. flex
    external_ids            = Column(JSON, nullable=True)      # {"batch_id": "...", "response_id": "...", "run_id": "..."}
    vector_store_id         = Column(String, nullable=True) # 若有用 file_search

    # 狀態流轉（覆蓋 batch/bg 生命週期）
    status                  = Column(String, nullable=False, default="queued")
    # 可能值：queued/submitted/running/succeeded/failed/canceled

    # 成本與統計（保留原有 prompt/completion，新增明細）
    prompt_tokens           = Column(Integer, nullable=True)
    completion_tokens       = Column(Integer, nullable=True)
    input_tokens            = Column(Integer, nullable=True)
    cached_input_tokens     = Column(Integer, nullable=True)
    output_tokens           = Column(Integer, nullable=True)

    cost_usd                = Column(Float, nullable=True)

    # 輸入/輸出與診斷
    request_payload_path    = Column(Text, nullable=True)  # 我們送出的 payload（選填）
    response_path           = Column(Text, nullable=True)         # 最終結果存放路徑（JSON）
    error                   = Column(Text, nullable=True)
    retry_count             = Column(Integer, nullable=False, default=0)
    schema_version          = Column(String, nullable=True)      # 你 resources/openai/response_format 版本號

    # 事件時間
    created_at              = Column(AwareDateTime, nullable=False)
    submitted_at            = Column(AwareDateTime, nullable=True)  # 對外送出時間（batch/bg）
    started_at              = Column(AwareDateTime, nullable=True)    # 真正執行時間（可選）
    completed_at            = Column(AwareDateTime, nullable=True)

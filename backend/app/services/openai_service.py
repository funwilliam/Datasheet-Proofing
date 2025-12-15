# backend/app/services/openai_service.py
from __future__ import annotations

from typing import List, Dict, Any, Optional, Literal, Tuple
from pathlib import Path
import traceback
import datetime
import json
import re

import openai
from sqlalchemy.orm import Session

from ..settings import settings
from ..models import (
    FileAsset,
    ModelItem,
    ModelApplicationTag,
    FileModelAppearance,
)

# ────────────────────────────── 檔案/常數 ──────────────────────────────

INSTR_DIR = Path(__file__).resolve().parents[3] / "resources" / "openai"
INST_GET_MODELS_PATH = INSTR_DIR / "system_instructions" / "擷取型號.md"
INST_EXTRACT_PATH    = INSTR_DIR / "system_instructions" / "擷取規格.md"
SCHEMA_GET_MODELS_PATH = INSTR_DIR / "response_format" / "擷取型號.json"
SCHEMA_EXTRACT_PATH    = INSTR_DIR / "response_format" / "擷取規格.json"

def _safe_read_text(p: Path, default: str = "") -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return default

def _safe_read_json(p: Path, default: dict | list | None = None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default

INST_GET_MODELS  = _safe_read_text(INST_GET_MODELS_PATH, default="")
INST_EXTRACT     = _safe_read_text(INST_EXTRACT_PATH, default="")
SCHEMA_GET_MODELS = _safe_read_json(SCHEMA_GET_MODELS_PATH, default=None)
SCHEMA_EXTRACT    = _safe_read_json(SCHEMA_EXTRACT_PATH, default=None)

EXTRACT_DIR = settings.WORKSPACE_DIR / "extractions"
EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

# --- pricing: 每 1M tokens 的 USD 單價 ---
PRICING_PER_1M = {
    "gpt-5":   {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-4.1": {"input": 2.00, "cached_input": 0.50,  "output": 8.00},
    "gpt-4o":  {"input": 2.50, "cached_input": 1.25,  "output": 10.00},
}

# OpenAI 可能回傳版本化 model 名稱，例如 "gpt-5-2025-10-03"；這裡只針對日期版本做定價歸一化，避免誤把 "gpt-4o-mini" 映射成 "gpt-4o"。
_VERSIONED_MODEL_RE = re.compile(r"^(gpt-5|gpt-4\\.1|gpt-4o)-\\d{4}-\\d{2}-\\d{2}(?:$|-)", re.IGNORECASE)

def _pricing_key_for_model(model: Optional[str]) -> Optional[str]:
    if not model:
        return None
    if model in PRICING_PER_1M:
        return model
    m = _VERSIONED_MODEL_RE.match(model)
    if m:
        key = m.group(1)
        return key if key in PRICING_PER_1M else None
    return None

# ────────────────────────────── 共用小工具 ──────────────────────────────

def _pick(v, *keys, default=None):
    """從物件或 dict 取第一個存在的 key 屬性，容錯不同 SDK 欄位命名。"""
    for k in keys:
        try:
            if isinstance(v, dict):
                if k in v and v[k] is not None:
                    return v[k]
            else:
                val = getattr(v, k, None)
                if val is not None:
                    return val
        except Exception:
            pass
    return default

def _to_int(x) -> int:
    try:
        return int(x)
    except Exception:
        return 0

def _extract_usage(resp) -> dict:
    """
    統一從 Responses 回傳取用量。
    - input_tokens: usage.input_tokens / usage.prompt_tokens
    - output_tokens: usage.output_tokens / usage.completion_tokens
    - cached_input: 優先 sum(cache_read_input_tokens, cache_write_input_tokens)；
                    若兩者皆無，再退回 cached_input_tokens / cached_tokens。
    """
    u = getattr(resp, "usage", None) or {}

    # 基本
    input_tokens  = _to_int(_pick(u, "input_tokens", "prompt_tokens", default=0))
    output_tokens = _to_int(_pick(u, "output_tokens", "completion_tokens", default=0))

    # 盡量避免低估：若同時存在 read/write 就加總；否則回退 aggregate 欄位
    read_cached   = _to_int(_pick(u, "cache_read_input_tokens", "cached_read_input_tokens", default=0))
    write_cached  = _to_int(_pick(u, "cache_write_input_tokens", "cached_write_input_tokens", default=0))
    aggregate     = _to_int(_pick(u, "cached_input_tokens", "cached_tokens", default=0))

    cached_input = (read_cached + write_cached) if (read_cached + write_cached) > 0 else aggregate
    return {"input": input_tokens, "cached_input": cached_input, "output": output_tokens}

def _acc(a: dict, b: dict) -> dict:
    return {
        "input": a.get("input", 0) + b.get("input", 0),
        "cached_input": a.get("cached_input", 0) + b.get("cached_input", 0),
        "output": a.get("output", 0) + b.get("output", 0),
    }

def _calc_cost(
    model: str,
    usage: dict,
    mode: str,
    service_tier: Optional[Literal["auto", "default", "flex", "priority", "scale"]] = None
) -> float:
    """
    依 model 與使用量計價。
    折扣/加成：
      - service_tier == 'flex'   → 0.5x
      - mode == 'batch'          → 0.5x
      - service_tier in {'priority','scale'} → 2.0x
    """
    pricing_key = _pricing_key_for_model(model)
    rate = PRICING_PER_1M.get(pricing_key) if pricing_key else None
    if not rate:
        return 0.0

    mult = 1.0
    if service_tier == "flex" or mode == "batch":
        mult = 0.5
    elif service_tier in ("priority", "scale"):
        mult = 2.0

    cost = (
        usage["input"] * (rate["input"] / 1_000_000.0) +
        usage["cached_input"] * (rate["cached_input"] / 1_000_000.0) +
        usage["output"] * (rate["output"] / 1_000_000.0)
    )
    return round(cost * mult, 6)

def _resolve_model_and_tier(
    resp,
    fallback_model: str,
    fallback_tier: Optional[Literal["auto", "default", "flex", "priority", "scale"]] = None
) -> tuple[str, Optional[Literal["auto", "default", "flex", "priority", "scale"]]]:
    """從回傳物件拿到實際使用的 model / service_tier；若沒有就用傳入的 fallback。"""
    model = _pick(resp, "model", default=fallback_model) or fallback_model
    tier  = _pick(resp, "service_tier", default=fallback_tier)
    return model, tier  # type: ignore[return-value]

# ────────────────────────────── JSON Schema 調用 ──────────────────────────────

def _get_model_numbers(
    client: openai.OpenAI,
    *,
    model_name: str,
    service_tier: Optional[Literal["auto", "default", "flex", "priority", "scale"]] = None,
    file: Optional[openai.types.FileObject] = None,
) -> dict:
    """
    取出型號清單（以 json_schema 結構化輸出）。
    回傳：
    {
      "models": List[str],
      "usage": {"input": int, "cached_input": int, "output": int},
      "model": str,
      "service_tier": Optional[str],
    }
    """
    try:
        kwargs = dict(
            model=model_name,
            instructions=INST_GET_MODELS,
            input=[{
                "role": "user",
                "content": [
                    *([{"type": "input_file", "file_id": file.id}] if file else []),
                    {"type": "input_text",
                     "text": "請根據指令回傳數據。沒看到檔案就說沒看到檔案，沒看到指令就說沒看到指令。"},
                ],
            }],
            text={"format": SCHEMA_GET_MODELS},
            timeout=900,
        )
        if service_tier:
            kwargs["service_tier"] = service_tier

        resp = client.responses.create(**kwargs)

        usage = _extract_usage(resp)
        actual_model, actual_tier = _resolve_model_and_tier(resp, model_name, service_tier)

        text = (getattr(resp, "output_text", "") or "").strip()
        models = []
        if text:
            try:
                data = json.loads(text)
                models = data.get("models", []) or []
            except Exception:
                models = []

        return {
            "models": models,
            "usage": usage,
            "model": actual_model,
            "service_tier": actual_tier,
        }

    except Exception:
        traceback.print_exc()
        return {
            "models": [],
            "usage": {"input": 0, "cached_input": 0, "output": 0},
            "model": model_name,
            "service_tier": service_tier,
        }

def _run_extraction(
    client: openai.OpenAI,
    *,
    models: List[str],
    model_name: str,
    service_tier: Optional[Literal["auto", "default", "flex", "priority", "scale"]] = None,
    file: Optional[openai.types.FileObject] = None,
) -> dict:
    """
    對一批 models 做欄位擷取，Responses + json_schema。
    回傳：
    {
      "items": List[dict],   # schema 的 key 為 "models"
      "usage": {"input": int, "cached_input": int, "output": int},
      "model": str,
      "service_tier": Optional[str],
    }
    """
    try:
        payload = {
            "request_type": "Datasheet_Parsing_Request",
            "models": list(models),
        }

        kwargs = dict(
            model=model_name,
            instructions=INST_EXTRACT,
            input=[{
                "role": "user",
                "content": [
                    *([{"type": "input_file", "file_id": file.id}] if file else []),
                    {"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)},
                ],
            }],
            text={"format": SCHEMA_EXTRACT},
            timeout=900,
        )
        if service_tier:
            kwargs["service_tier"] = service_tier

        resp = client.responses.create(**kwargs)

        usage = _extract_usage(resp)
        actual_model, actual_tier = _resolve_model_and_tier(resp, model_name, service_tier)

        text = (getattr(resp, "output_text", "") or "").strip()
        items: List[Dict[str, Any]] = []
        if text:
            try:
                data = json.loads(text)
                items = data.get("models", []) or []
            except Exception:
                items = []

        return {
            "items": items,
            "usage": usage,
            "model": actual_model,
            "service_tier": actual_tier,
        }

    except Exception:
        traceback.print_exc()
        return {
            "items": [],
            "usage": {"input": 0, "cached_input": 0, "output": 0},
            "model": model_name,
            "service_tier": service_tier,
        }

# ────────────────────────────── 欄位轉換/差異判斷（依 schema） ──────────────────────────────
from typing import Tuple, Optional, Dict, Any, List


def _norm_field(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None

def _join_with_unit_range(lower: Optional[str], upper: Optional[str]) -> Optional[str]:
    """
    嘗試把 lower/upper 轉成「{lower_val}~{upper_val} {unit}」。
    若左右單位不同或抓不到單位，退回 "lower~upper" 或 None。
    """
    if not lower or not upper:
        return None

    def strip_unit(value: str):
        # 允許 ±、數字與小數、單位字母/μ/% 等
        m = re.match(r"±?([\d.]+)\s*([a-zA-Zμ%]+)", value.strip())
        return m.groups() if m else (value.strip(), "")

    l_val, l_unit = strip_unit(lower)
    u_val, u_unit = strip_unit(upper)
    if l_unit and l_unit == u_unit:
        return f"{l_val}~{u_val} {l_unit}"
    return f"{lower}~{upper}"

def _project_item_from_schema(raw: Dict[str, Any]) -> Tuple[str, Dict[str, Optional[str]], List[str]]:
    """
    從「擷取規格.json」的單一 model 物件轉成：
      - model_number: str
      - fields: Dict[str, Optional[str]]  對應 ModelItem 目前有的欄位
      - apps: List[str]                   交給 ModelApplicationTag
    不存在或空白就回 None / 空陣列。
    """
    model_number = _norm_field(raw.get("Model Number"))

    # Input Voltage
    iv = raw.get("Input Voltage", {}) or {}
    input_voltage_range = _join_with_unit_range(_norm_field(iv.get("lower")), _norm_field(iv.get("upper")))
    # 你目前 DB 沒有 input_voltage_nominal，所以這裡先不存 nominal

    # Output Voltage
    ov = raw.get("Output Voltage", {}) or {}
    ov_value = _norm_field(ov.get("value"))
    ov_dual  = bool(ov.get("dual_output"))
    output_voltage = f"±{ov_value}" if (ov_value and ov_dual) else ov_value

    # Output Current（DB 目前沒有此欄位 → 不存）
    # oc = raw.get("Output Current", {}) or {}

    # Output Power
    op = raw.get("Output Power", {}) or {}
    output_power = _norm_field(op.get("value"))

    # Package
    pkg = raw.get("Package", {}) or {}
    package = _norm_field(pkg.get("value"))

    # I/O Isolation
    iso = raw.get("I/O Isolation", {}) or {}
    isolation = _norm_field(iso.get("value"))

    # Insulation System
    ins = raw.get("Insulation System", {}) or {}
    insulation = _norm_field(ins.get("value"))

    # Application → List[str]
    app = raw.get("Application", {}) or {}
    apps_raw = app.get("values") or []
    apps: List[str] = [s.strip() for s in apps_raw if isinstance(s, str) and s.strip()]

    # Dimension
    dim = raw.get("Dimension", {}) or {}
    length = _norm_field(dim.get("length"))
    width  = _norm_field(dim.get("width"))
    height = _norm_field(dim.get("height"))
    if length and width and height:
        dimension = f"{length} x {width} x {height}"
    else:
        dimension = None

    # Efficiency（DB 目前沒有此欄位 → 不存）
    # eff = raw.get("Efficiency", {}) or {}

    fields: Dict[str, Optional[str]] = {
        "input_voltage_range": input_voltage_range,
        "output_voltage": output_voltage,
        "output_power": output_power,
        "package": package,
        "isolation": isolation,
        "insulation": insulation,
        "dimension": dimension,
    }
    return model_number or "", fields, apps

def _fields_changed(mi: ModelItem, new_fields: Dict[str, Optional[str]]) -> bool:
    for col, new_val in new_fields.items():
        old_val = getattr(mi, col, None)
        if _norm_field(old_val) != _norm_field(new_val):
            return True
    return False

def _apps_changed(mi: ModelItem, new_apps: List[str]) -> bool:
    old_set = {t.app_tag_canon for t in (mi.applications or [])}
    new_set = {a.lower().strip() for a in new_apps if a and a.strip()}
    return old_set != new_set

def _upsert_model_and_apps(
    db: Session,
    model_number: str,
    fields: Dict[str, Optional[str]],
    apps: List[str],
) -> tuple[ModelItem, bool]:
    """
    以 model_number upsert ModelItem；只在「資料真的變動」時覆寫。
    變動規則：
      - 欄位或 tags 任一不同 → 視為變動
      - 若原 verify_status 為 'verified'，變動時改回 'unverified' 並清 reviewer/reviewed_at
    回傳：(ModelItem, changed_any)
    """
    mi = db.query(ModelItem).filter_by(model_number=model_number).one_or_none()
    is_new = mi is None
    if mi is None:
        mi = ModelItem(model_number=model_number, verify_status="unverified")
        db.add(mi)

    fields_diff = _fields_changed(mi, fields)
    apps_diff   = _apps_changed(mi, apps)
    changed_any = is_new or fields_diff or apps_diff

    if changed_any:
        # 覆寫欄位
        for col, val in fields.items():
            setattr(mi, col, _norm_field(val))

        # Applications 全量替換（刪除不存在、補新增）
        old = {t.app_tag_canon: t for t in (mi.applications or [])}
        new_canon = {t.lower().strip() for t in apps if t and t.strip()}

        # 刪除
        for canon, row in list(old.items()):
            if canon not in new_canon:
                db.delete(row)

        # 新增
        for tag in apps:
            canon = tag.lower().strip()
            if canon and canon not in old:
                db.add(ModelApplicationTag(model=mi, app_tag=tag, app_tag_canon=canon))

        # 審核狀態：verified → unverified
        if mi.verify_status == "verified":
            mi.verify_status = "unverified"
            mi.reviewer = None
            mi.reviewed_at = None

    return mi, changed_any

def _ensure_file_model_link(db: Session, file_hash: str, model_number: str):
    """
    確保 (file_hash, model_number) 的出現關聯存在。
    """
    exists = (
        db.query(FileModelAppearance)
        .filter_by(file_hash=file_hash, model_number=model_number)
        .one_or_none()
    )
    if exists is None:
        db.add(FileModelAppearance(file_hash=file_hash, model_number=model_number))

# ────────────────────────────── 主流程：擷取 ──────────────────────────────

def extract_with_openai(
    db: Session,
    file_hash: str,
    force_rerun: bool = False,
    *,
    model_name: str = "gpt-5",
    service_tier: Optional[Literal["auto", "default", "flex", "priority", "scale"]] = None,
    mode: str = "sync",  # 'sync' / 'batch' / 'background'
) -> dict:
    """
    回傳 dict:
    {
        "out_path": str,
        "cost_usd": float,
        "prompt_tokens": int,        # input + cached_input
        "completion_tokens": int,    # output
        "model": str,
        "service_tier": str|None,
        "usage": {"input": int, "cached_input": int, "output": int}
    }
    """
    fa: FileAsset = db.get(FileAsset, file_hash)
    if not fa:
        raise RuntimeError(f"file_hash not found: {file_hash}")

    if not settings.OPENAI_API_KEY or not settings.OPENAI_API_KEY.strip():
        raise RuntimeError("OPENAI_API_KEY is not set")

    out_path = EXTRACT_DIR / f"{file_hash}.json"

    # 若已存在結果且不強制重跑：直接回傳
    if not force_rerun and out_path.exists():
        return {
            "out_path": str(out_path),
            "cost_usd": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "model": None,
            "service_tier": None,
            "usage": {"input": 0, "cached_input": 0, "output": 0},
            "status": "canceled"
        }

    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    file = None
    total_usage = {"input": 0, "cached_input": 0, "output": 0}
    actual_model: str = model_name
    actual_tier: Optional[Literal["auto", "default", "flex", "priority", "scale"]] = service_tier

    try:
        data = Path(fa.local_path).read_bytes()
        file = client.files.create(file=(fa.filename or "datasheet.pdf", data), purpose="assistants")

        # 1) 取得型號清單
        gm = _get_model_numbers(
            client,
            model_name=model_name,
            service_tier=service_tier,
            file=file,
        )
        total_usage = _acc(total_usage, gm["usage"])
        actual_model = gm["model"] or actual_model
        actual_tier  = gm["service_tier"] or actual_tier
        models_list: List[str] = gm["models"]

        # 2) 分批擷取
        batch_size = 10
        model_batches = [models_list[i:i + batch_size] for i in range(0, len(models_list), batch_size)]
        merged: List[Dict[str, Any]] = []
        for batch in model_batches:
            if not batch:
                continue
            ex = _run_extraction(
                client,
                models=batch,
                model_name=model_name,
                service_tier=service_tier,
                file=file,
            )
            total_usage = _acc(total_usage, ex["usage"])
            actual_model = ex["model"] or actual_model
            actual_tier  = ex["service_tier"] or actual_tier
            merged.extend(ex["items"])

        # 3) 寫出聚合結果 JSON
        resp_obj = {"models": merged, "file_hash": file_hash, "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        out_path.write_text(json.dumps(resp_obj, ensure_ascii=False, indent=2), encoding="utf-8")

        # 4) 更新 DB（差異比對；只在變動時 unverified；並建立 FileModelAppearance）
        if force_rerun:
            # 重新整理本檔案的出現關聯（不刪 ModelItem 本體）
            db.query(FileModelAppearance).filter_by(file_hash=file_hash).delete()
            db.commit()

        upserted_model_numbers: List[str] = []
        for item in merged:
            model_number, fields, apps = _project_item_from_schema(item)
            if not model_number:
                continue

            mi, _changed = _upsert_model_and_apps(db, model_number, fields, apps)
            _ensure_file_model_link(db, file_hash, model_number)
            upserted_model_numbers.append(model_number)

        db.commit()

        # 5) 計價與回傳
        cost_usd = _calc_cost(actual_model, total_usage, mode, actual_tier)
        prompt_total = total_usage["input"] + total_usage["cached_input"]
        completion_total = total_usage["output"]

        return {
            "out_path": str(out_path),
            "cost_usd": cost_usd,
            "prompt_tokens": prompt_total,
            "completion_tokens": completion_total,
            "model": actual_model,
            "service_tier": actual_tier,
            "usage": total_usage,
            "status": "succeeded"
        }

    except Exception:
        traceback.print_exc()
        raise
    finally:
        try:
            if file:
                client.files.delete(file.id)
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass

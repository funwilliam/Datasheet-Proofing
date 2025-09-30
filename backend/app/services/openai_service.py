from __future__ import annotations
from typing import List, Dict, Any, Optional, Literal
from pathlib import Path
import json
import traceback

import openai
from sqlalchemy.orm import Session

from ..settings import settings
from ..models import ModelItem, FileAsset


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


INSTR_DIR = Path(__file__).resolve().parents[3] / "resources" / "openai"
INST_GET_MODELS_PATH = INSTR_DIR / "system_instructions" / "擷取型號.md"
INST_EXTRACT_PATH = INSTR_DIR / "system_instructions" / "擷取規格.md"
SCHEMA_GET_MODELS_PATH = INSTR_DIR / "response_format" / "擷取型號.json"
SCHEMA_EXTRACT_PATH = INSTR_DIR / "response_format" / "擷取規格.json"
INST_GET_MODELS = _safe_read_text(INST_GET_MODELS_PATH, default="")
INST_EXTRACT = _safe_read_text(INST_EXTRACT_PATH, default="")
SCHEMA_GET_MODELS = _safe_read_json(SCHEMA_GET_MODELS_PATH, default=None)
SCHEMA_EXTRACT = _safe_read_json(SCHEMA_EXTRACT_PATH, default=None)

EXTRACT_DIR = settings.WORKSPACE_DIR / "extractions"

# --- pricing: 每 1M tokens 的 USD 單價 ---
PRICING_PER_1M = {
    "gpt-5":   {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-4.1": {"input": 2.00, "cached_input": 0.50,  "output": 8.00},
    "gpt-4o":  {"input": 2.50, "cached_input": 1.25,  "output": 10.00},
}


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
    input_tokens = _to_int(_pick(u, "input_tokens", "prompt_tokens", default=0))
    output_tokens = _to_int(_pick(u, "output_tokens", "completion_tokens", default=0))

    # 盡量避免低估：若同時存在 read/write 就加總；否則回退 aggregate 欄位
    read_cached = _to_int(_pick(u, "cache_read_input_tokens", "cached_read_input_tokens", default=0))
    write_cached = _to_int(_pick(u, "cache_write_input_tokens", "cached_write_input_tokens", default=0))
    aggregate_cached = _to_int(_pick(u, "cached_input_tokens", "cached_tokens", default=0))

    if (read_cached + write_cached) > 0:
        cached_input = read_cached + write_cached
    else:
        cached_input = aggregate_cached

    return {"input": input_tokens, "cached_input": cached_input, "output": output_tokens}


def _acc(a: dict, b: dict) -> dict:
    return {
        "input": (a.get("input", 0) + b.get("input", 0)),
        "cached_input": (a.get("cached_input", 0) + b.get("cached_input", 0)),
        "output": (a.get("output", 0) + b.get("output", 0)),
    }


def _calc_cost(
    model: str,
    usage: dict,
    mode: str,
    service_tier: Optional[Literal["auto", "default", "flex", "priority", "scale"]] = None
) -> float:
    """
    依 model 與使用量計價。
    折扣/加成規則：
      - service_tier == 'flex'   → 0.5x
      - mode == 'batch'          → 0.5x
      - service_tier in {'priority','scale'} → 2.0x
      - auto/default/None → 1.0x
    background 不影響價格。
    """
    rate = PRICING_PER_1M.get(model)
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
    tier = _pick(resp, "service_tier", default=fallback_tier)
    return model, tier  # type: ignore[return-value]


def _get_model_numbers(
    client: openai.OpenAI,
    *,
    model_name: str,
    service_tier: Optional[Literal["auto", "default", "flex", "priority", "scale"]] = None,
    vector_store: openai.types.VectorStore | None = None,
    file: openai.types.FileObject | None = None,
) -> dict:
    """
    用 Responses + file_search 取出型號清單（以 json_schema 結構化輸出）。
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
            tools=([{
                "type": "file_search",
                "vector_store_ids": [vector_store.id],
                "max_num_results": 20,
            }] if vector_store else None),
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
    vector_store: openai.types.VectorStore | None = None,
    file: openai.types.FileObject | None = None,
) -> dict:
    """
    對一批 models 做欄位擷取，Responses + file_search + json_schema。
    回傳：
    {
      "items": List[dict],
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
            tools=([{
                "type": "file_search",
                "vector_store_ids": [vector_store.id],
                "max_num_results": 20,
            }] if vector_store else None),
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
                items = data.get("models", []) or []   # schema 的 key 為 "models"
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


def extract_with_openai(
    db: Session,
    file_hash: str,
    force_rerun: bool = False,
    *,
    model_name: str = "gpt-5",
    service_tier: Optional[Literal["auto", "default", "flex", "priority", "scale"]] = None,
    mode: str = "sync",                      # 'sync' / 'batch' / 'background'
) -> dict:
    """
    回傳 dict:
    {
        "out_path": str,
        "cost_usd": float,
        "prompt_tokens": int,        # input + cached_input
        "completion_tokens": int,    # output
        "model": str,
        "service_tier": str|None,    # 回傳的實際 tier
        "usage": {"input": int, "cached_input": int, "output": int}
    }
    """
    fa: FileAsset = db.get(FileAsset, file_hash)
    if not fa:
        raise RuntimeError(f"file_hash not found: {file_hash}")

    if settings.OPENAI_API_KEY is None or not settings.OPENAI_API_KEY.strip():
        raise RuntimeError("OPENAI_API_KEY is not set")

    # 若已解析且不強制重跑：回傳現有輸出路徑與零用量
    if not force_rerun:
        exists = db.query(ModelItem).filter(ModelItem.file_hash == file_hash).first()
        if exists:
            out_path = EXTRACT_DIR / f"{file_hash}.json"
            return {
                "out_path": str(out_path),
                "cost_usd": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "model": model_name,
                "service_tier": service_tier,
                "usage": {"input": 0, "cached_input": 0, "output": 0},
            }

    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    file = None
    total_usage = {"input": 0, "cached_input": 0, "output": 0}
    actual_model: str = model_name
    actual_tier: Optional[Literal["auto", "default", "flex", "priority", "scale"]] = service_tier

    try:
        data = Path(fa.local_path).read_bytes()
        # 同一個 file.id 在整個流程內重用（提升命中 cached_input 的機會）
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
        actual_tier = gm["service_tier"] or actual_tier
        models_list = gm["models"]

        # 2) 逐批擷取
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
            actual_tier = ex["service_tier"] or actual_tier
            merged.extend(ex["items"])

        # 3) 寫出聚合結果 JSON
        resp_obj = {"models": merged}
        out_path = EXTRACT_DIR / f"{file_hash}.json"
        out_path.write_text(json.dumps(resp_obj, ensure_ascii=False, indent=2), encoding="utf-8")

        # 4) 更新 DB（維持你原本策略）
        if force_rerun:
            db.query(ModelItem).filter(ModelItem.file_hash == file_hash).delete()
            db.commit()
        for m in merged:
            key = str(m.get("Model Number", "N/A"))
            payload = json.dumps(m, ensure_ascii=False)
            existing = db.query(ModelItem).filter_by(file_hash=file_hash, model_number=key).first()
            if existing:
                existing.fields_json = payload
                existing.verify_status = "pending"
            else:
                db.add(ModelItem(file_hash=file_hash, model_number=key, fields_json=payload, verify_status="pending"))
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
        client.close()

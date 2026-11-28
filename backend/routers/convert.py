import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import update
from sqlmodel import Session, desc, select

from database import DB_MODE, _get_clean_env, get_session
from models import BatchJob, BatchJobItem, ConversionRecord, DailyQuota, ModelCodeMapping, utc_now_str, utc_today_str
from services.auth_service import require_auth
from services.b2_storage import (
    b2_is_enabled,
    delete_prefix_objects,
    get_b2_bucket_name,
    get_storage_stats,
    load_object,
    purge_bucket_files,
    save_object,
)
from services.excel_exporter import (
    build_batch_zip_bytes,
    build_single_xlsx_bytes,
    build_xlsx_bytes,
    export_tables_package,
    make_download_headers,
)
from services.model_dict_service import (
    apply_model_dict_to_result,
    clear_all_mappings,
    count_mappings,
    delete_mapping,
    list_mappings,
    upsert_mappings,
)
from services.ocr_service import (
    extract_tables_from_image,
    get_active_model,
    get_gemini_key_count,
    has_gemini_key,
    merge_quotation_slices,
)

router = APIRouter(prefix="/api", tags=["convert"], dependencies=[Depends(require_auth)])

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".heic", ".heif"}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_DAILY_LIMIT = 50
QUOTA_CONFIG_KEY = "__config__"
CLIENT_LOCK_PREFIX = "__client__:"
STALE_RUNNING_SECONDS = 180
ACTIVE_HEARTBEAT_WINDOW_SECONDS = 120
MAX_CUSTOM_DAILY_LIMIT = 100000


class UpdateQuotaRequest(BaseModel):
    daily_limit: Optional[int] = None
    unlimited: Optional[bool] = None
    reset_used: bool = False


class HeartbeatRequest(BaseModel):
    client_id: str = ""


class SheetPayload(BaseModel):
    name: str = "Sheet1"
    export_filename: Optional[str] = None
    headers: List[str]
    col_types: List[str] = []
    rows: List[List[str]] = []
    merges: List[Dict[str, int]] = []
    warnings: List[Dict[str, Any]] = []
    section_header_rows: List[int] = []
    source_filename: Optional[str] = None
    style_preset: Optional[str] = None


class ExportRequest(BaseModel):
    sheets: List[SheetPayload]
    output_mode: str = "all_in_one"  # all_in_one | per_image
    format: str = "xlsx"  # xlsx | csv
    filename: str = "报价单导出"


class CreateJobRequest(BaseModel):
    title: str = "批量报价单任务"
    mode: str = "excel"
    output_mode: str = "per_image"
    merge_per_image: bool = True
    total_items: int = 1
    client_id: str = ""


class QuotationGroupPayload(BaseModel):
    source_filename: str
    quote_title: str
    document_notes: str = ""
    engine: str = "demo"
    is_demo: bool = False
    notice: str = ""
    merged_sheet: SheetPayload
    raw_sheets: List[SheetPayload] = []


class FinalizeJobRequest(BaseModel):
    quotations: List[QuotationGroupPayload] = []
    merge_per_image: bool = True
    output_mode: str = "per_image"


def _safe_filename(raw_name: str, fallback: str = "报价单") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\[\]]+", "_", (raw_name or fallback).strip()).strip("_.") or fallback
    return cleaned[:48]


def _parse_utc_str(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    s = str(ts).strip().replace("T", " ").rstrip("Z")
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _seconds_since(ts: Optional[str]) -> float:
    dt = _parse_utc_str(ts)
    if not dt:
        return 999999.0
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _save_client_lock(session: Session, job_uid: str, client_id: str) -> None:
    if not client_id:
        return
    lock_key = f"{CLIENT_LOCK_PREFIX}{job_uid}"
    row = session.exec(select(DailyQuota).where(DailyQuota.quota_date == lock_key)).first()
    now_str = utc_now_str()
    if not row:
        row = DailyQuota(
            quota_date=lock_key,
            client_key=client_id[:64],
            used_count=0,
            daily_limit=0,
            updated_at=now_str,
        )
    else:
        row.client_key = client_id[:64]
        row.updated_at = now_str
    session.add(row)


def _get_client_lock(session: Session, job_uid: str) -> str:
    row = session.exec(select(DailyQuota).where(DailyQuota.quota_date == f"{CLIENT_LOCK_PREFIX}{job_uid}")).first()
    return str(row.client_key) if row else ""


def _delete_client_lock(session: Session, job_uid: str) -> None:
    rows = session.exec(select(DailyQuota).where(DailyQuota.quota_date == f"{CLIENT_LOCK_PREFIX}{job_uid}")).all()
    for r in rows:
        session.delete(r)


def _reconcile_job_state(
    session: Session,
    job: BatchJob,
    items: Optional[List[BatchJobItem]] = None,
) -> tuple[int, int, bool]:
    """
    以真实的 BatchJobItem 行校准批次已完成/失败数，彻底杜绝并发提交时的计数丢失（如 19 份显示 18/19）；
    并自动修复超过 3 分钟无心跳的僵尸 running 状态。
    """
    if items is None:
        items = list(
            session.exec(
                select(BatchJobItem).where(BatchJobItem.job_uid == job.job_uid).order_by(BatchJobItem.item_index)
            ).all()
        )
    real_completed = sum(1 for it in items if it.status == "completed")
    real_failed = sum(1 for it in items if it.status == "failed")
    changed = False

    if job.completed_items != real_completed:
        job.completed_items = real_completed
        changed = True
    if job.failed_items != real_failed:
        job.failed_items = real_failed
        changed = True

    if job.status == "running":
        age_sec = _seconds_since(job.updated_at or job.created_at)
        if age_sec > STALE_RUNNING_SECONDS:
            if real_completed >= job.total_items and real_completed > 0:
                job.status = "completed"
            elif real_completed > 0:
                job.status = "partial_failed"
            else:
                job.status = "failed"
            _delete_client_lock(session, job.job_uid)
            changed = True

    if changed:
        session.add(job)
    return real_completed, real_failed, changed


def _build_zip_from_b2_items(job: BatchJob, completed_items: List[BatchJobItem]) -> tuple[bytes, str]:
    """直接从 Backblaze B2 / 本地存储读取各已完成单表 .xlsx 并服务端打包 .zip（彻底规避 Vercel 4.5MB 请求体上限）。"""
    zip_buf = io.BytesIO()
    used_names: set = set()
    added_count = 0
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, it in enumerate(completed_items):
            if not it.xlsx_b2_key:
                continue
            raw_xlsx = load_object(it.xlsx_b2_key)
            if not raw_xlsx:
                continue
            raw_stem = Path(it.xlsx_filename or it.quote_title or f"表格_{idx + 1}").stem
            base = _safe_filename(raw_stem, f"表格_{idx + 1}")
            cand = base
            seq = 2
            while cand.lower() in used_names:
                cand = f"{base[:42]}_{seq}"
                seq += 1
            used_names.add(cand.lower())
            zf.writestr(f"{cand}.xlsx", raw_xlsx)
            added_count += 1
    if added_count == 0:
        return b"", ""
    zip_buf.seek(0)
    zip_name = f"{_safe_filename(job.title, '批量表格打包')}_{job.job_uid[:6]}.zip"
    return zip_buf.getvalue(), zip_name


def _get_default_daily_limit(session: Session) -> int:
    """读取持久化的默认每日额度配置（0 表示不限次）；优先数据库配置行，其次环境变量 DAILY_QUOTA_LIMIT，兜底 50。"""
    cfg = session.exec(
        select(DailyQuota).where(DailyQuota.quota_date == QUOTA_CONFIG_KEY, DailyQuota.client_key == "default")
    ).first()
    if cfg is not None:
        return max(0, int(cfg.daily_limit))
    env_raw = _get_clean_env("DAILY_QUOTA_LIMIT")
    if env_raw:
        try:
            return max(0, min(MAX_CUSTOM_DAILY_LIMIT, int(env_raw)))
        except ValueError:
            pass
    return DEFAULT_DAILY_LIMIT


def _format_quota_payload(quota: DailyQuota) -> Dict[str, Any]:
    is_unlimited = int(quota.daily_limit) <= 0
    return {
        "quota_date": quota.quota_date,
        "used_count": int(quota.used_count),
        "daily_limit": int(quota.daily_limit),
        "unlimited": is_unlimited,
        "remaining": -1 if is_unlimited else max(0, int(quota.daily_limit) - int(quota.used_count)),
    }


def _get_or_create_today_quota(session: Session) -> DailyQuota:
    today = utc_today_str()
    quota = session.exec(
        select(DailyQuota).where(DailyQuota.quota_date == today, DailyQuota.client_key == "default")
    ).first()
    if not quota:
        quota = DailyQuota(
            quota_date=today,
            client_key="default",
            used_count=0,
            daily_limit=_get_default_daily_limit(session),
            updated_at=utc_now_str(),
        )
        session.add(quota)
        session.commit()
        session.refresh(quota)
    return quota


def _validate_upload_image(up_file: UploadFile, raw_bytes: bytes, idx: int) -> str:
    fname = up_file.filename or f"image_{idx + 1}.png"
    ext = Path(fname).suffix.lower()
    if ext and ext not in ALLOWED_EXTENSIONS and not (up_file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail=f"不支持的文件格式：{fname}（仅支持 JPG/PNG/GIF/WEBP/HEIC）")
    if not raw_bytes:
        raise HTTPException(status_code=400, detail=f"文件内容为空：{fname}")
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail=f"图片 {fname} 超过 10MB 上限，请压缩后重试")
    return fname


@router.get("/quota")
def get_quota(session: Session = Depends(get_session)) -> Dict[str, Any]:
    quota = _get_or_create_today_quota(session)
    return {
        **_format_quota_payload(quota),
        "gemini_configured": has_gemini_key(),
        "gemini_key_count": get_gemini_key_count(),
        "model": get_active_model(),
        "b2_configured": b2_is_enabled(),
        "model_dict_count": count_mappings(session),
    }


@router.put("/quota")
def update_quota_config(req: UpdateQuotaRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """自定义每日额度（0 或 unlimited=True 表示不限次）或重置今日已用计数，并持久化至后续日期。"""
    quota = _get_or_create_today_quota(session)
    now_str = utc_now_str()

    new_limit: Optional[int] = None
    if req.unlimited is True:
        new_limit = 0
    elif req.daily_limit is not None:
        if req.daily_limit < 0 or req.daily_limit > MAX_CUSTOM_DAILY_LIMIT:
            raise HTTPException(status_code=400, detail=f"每日额度需在 0（不限次）至 {MAX_CUSTOM_DAILY_LIMIT} 之间")
        new_limit = int(req.daily_limit)
    elif req.unlimited is False and quota.daily_limit <= 0:
        new_limit = DEFAULT_DAILY_LIMIT

    if new_limit is not None:
        quota.daily_limit = new_limit
        cfg = session.exec(
            select(DailyQuota).where(DailyQuota.quota_date == QUOTA_CONFIG_KEY, DailyQuota.client_key == "default")
        ).first()
        if not cfg:
            cfg = DailyQuota(
                quota_date=QUOTA_CONFIG_KEY,
                client_key="default",
                used_count=0,
                daily_limit=new_limit,
                updated_at=now_str,
            )
        else:
            cfg.daily_limit = new_limit
            cfg.updated_at = now_str
        session.add(cfg)

    if req.reset_used:
        quota.used_count = 0

    quota.updated_at = now_str
    session.add(quota)
    session.commit()
    session.refresh(quota)
    return {
        **_format_quota_payload(quota),
        "gemini_configured": has_gemini_key(),
        "gemini_key_count": get_gemini_key_count(),
        "model": get_active_model(),
        "b2_configured": b2_is_enabled(),
        "model_dict_count": count_mappings(session),
    }


class ModelDictItemIn(BaseModel):
    model_name: str
    network_code: str
    brand_hint: str = ""
    source: str = ""


class ModelDictUpsertRequest(BaseModel):
    items: List[ModelDictItemIn] = []
    source: str = "manual"


@router.get("/model-dict")
def get_model_dict(
    q: str = Query(default=""),
    limit: int = Query(default=500, ge=1, le=2000),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """查询云端「机型 ↔ 网络型号」记忆库（支持关键字模糊搜索）。"""
    rows = list_mappings(session, query=q, limit=limit)
    total = count_mappings(session)
    return {
        "total": total,
        "items": [r.model_dump() for r in rows],
    }


@router.post("/model-dict/upsert")
def upsert_model_dict(
    req: ModelDictUpsertRequest,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """新增或更新 1 条或批量「机型 ↔ 网络型号」映射（支持工作台修完自动记住、整表收录与粘贴批量导入）。"""
    if not req.items:
        return {"created": 0, "updated": 0, "skipped": 0, "total": count_mappings(session)}
    if len(req.items) > 1000:
        raise HTTPException(status_code=400, detail="单次最多批量导入 1000 条机型映射")
    return upsert_mappings(
        session,
        entries=[it.model_dump() for it in req.items],
        default_source=req.source or "manual",
    )


@router.delete("/model-dict/{mapping_id}")
def remove_model_dict_item(
    mapping_id: int,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    ok = delete_mapping(session, mapping_id)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到该机型映射记录")
    return {"deleted": True, "id": mapping_id, "total": count_mappings(session)}


@router.delete("/model-dict")
def clear_model_dict(session: Session = Depends(get_session)) -> Dict[str, Any]:
    deleted = clear_all_mappings(session)
    return {"cleared": True, "deleted": deleted, "total": 0}


@router.get("/history")
def list_history(session: Session = Depends(get_session)) -> List[ConversionRecord]:
    records = session.exec(select(ConversionRecord).order_by(desc(ConversionRecord.id)).limit(30)).all()
    return list(records)


@router.delete("/history/{record_id}")
def delete_history(record_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    record = session.get(ConversionRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="未找到对应的转换记录")
    session.delete(record)
    session.commit()
    return {"deleted": True, "id": record_id}


@router.post("/jobs")
def create_batch_job(req: CreateJobRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    if req.total_items < 1 or req.total_items > 20:
        raise HTTPException(status_code=400, detail="单批次支持 1~20 张表格图片")

    job_uid = uuid.uuid4().hex[:12]
    job = BatchJob(
        job_uid=job_uid,
        title=_safe_filename(req.title, "批量表格任务"),
        mode=req.mode,
        output_mode=req.output_mode,
        merge_per_image=req.merge_per_image,
        total_items=req.total_items,
        completed_items=0,
        failed_items=0,
        status="running",
        storage_backend="b2" if b2_is_enabled() else "local",
        created_at=utc_now_str(),
        updated_at=utc_now_str(),
    )
    session.add(job)
    if req.client_id:
        _save_client_lock(session, job_uid, req.client_id)
    session.commit()
    session.refresh(job)
    return job.model_dump()


@router.get("/jobs/active")
def get_active_running_jobs(
    client_id: str = Query(default=""),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """查询当前是否有正在运行中的批处理任务（用于多台电脑同时打开时友好提示「另一台设备正在跑」）。"""
    running_jobs = list(
        session.exec(select(BatchJob).where(BatchJob.status == "running").order_by(desc(BatchJob.id)).limit(10)).all()
    )
    active_list: List[Dict[str, Any]] = []
    other_active: List[Dict[str, Any]] = []
    any_reconciled = False

    for j in running_jobs:
        real_completed, _, changed = _reconcile_job_state(session, j)
        if changed:
            any_reconciled = True
        if j.status != "running":
            continue
        age_sec = _seconds_since(j.updated_at or j.created_at)
        if age_sec > ACTIVE_HEARTBEAT_WINDOW_SECONDS:
            continue
        owner_cid = _get_client_lock(session, j.job_uid)
        is_self = bool(client_id and owner_cid and owner_cid == client_id)
        info = {
            "job_uid": j.job_uid,
            "title": j.title,
            "completed_items": real_completed,
            "total_items": j.total_items,
            "created_at": j.created_at,
            "updated_at": j.updated_at,
            "elapsed_seconds": int(_seconds_since(j.created_at)),
            "is_self": is_self,
        }
        active_list.append(info)
        if not is_self:
            other_active.append(info)

    if any_reconciled:
        session.commit()

    return {
        "has_other_active": len(other_active) > 0,
        "other_active_jobs": other_active,
        "active_jobs": active_list,
    }


@router.post("/jobs/{job_uid}/heartbeat")
def job_heartbeat(
    job_uid: str,
    req: HeartbeatRequest,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """运行中客户端每 20 秒续期心跳，确保多电脑实时感知运行进度，断线 2 分钟自动失效。"""
    job = session.exec(select(BatchJob).where(BatchJob.job_uid == job_uid)).first()
    if not job:
        raise HTTPException(status_code=404, detail="未找到对应的批处理任务")
    if job.status == "running":
        job.updated_at = utc_now_str()
        real_completed, _, _ = _reconcile_job_state(session, job)
        if req.client_id:
            _save_client_lock(session, job_uid, req.client_id)
        session.add(job)
        session.commit()
        return {"ok": True, "completed_items": real_completed, "total_items": job.total_items}
    return {"ok": False, "status": job.status}


@router.post("/jobs/{job_uid}/resume")
def resume_batch_job(
    job_uid: str,
    req: HeartbeatRequest,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """
    断点续传恢复接口：
    - 校验云端批次仍存在，返回已在服务端归档完成的 item_index 列表及标题（避免重复识别已成功图片）；
    - 将批次状态重新置回 running 并续期客户端运行锁，续跑完成后与旧文件一起打包成同一份 .zip。
    """
    job = session.exec(select(BatchJob).where(BatchJob.job_uid == job_uid)).first()
    if not job:
        raise HTTPException(status_code=404, detail="原云端批次记录已不存在，将自动新建批次继续")

    items = list(
        session.exec(
            select(BatchJobItem).where(BatchJobItem.job_uid == job_uid).order_by(BatchJobItem.item_index)
        ).all()
    )
    completed_items = [it for it in items if it.status == "completed"]
    completed_indices = sorted({int(it.item_index) for it in completed_items})
    completed_meta = {
        str(int(it.item_index)): {
            "quote_title": it.quote_title,
            "row_count": int(it.row_count),
            "warning_count": int(it.warning_count),
        }
        for it in completed_items
    }

    job.status = "running"
    job.completed_items = len(completed_indices)
    job.updated_at = utc_now_str()
    if req.client_id:
        _save_client_lock(session, job_uid, req.client_id)
    session.add(job)
    session.commit()

    return {
        "job_uid": job.job_uid,
        "title": job.title,
        "total_items": int(job.total_items),
        "completed_indices": completed_indices,
        "completed_meta": completed_meta,
    }


class CommitQuotationRequest(BaseModel):
    item_index: int = 0
    source_filename: str
    file_size_bytes: int = 0
    mode: str = "excel"
    merge_per_image: bool = True
    doc_type: str = "auto"
    slice_results: List[Dict[str, Any]] = []
    failed_error: str = ""


def _persist_completed_quotation(
    session: Session,
    job: BatchJob,
    quota: DailyQuota,
    item_index: int,
    fname: str,
    file_size_bytes: int,
    mode: str,
    merge_per_image: bool,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    used_engine = result.get("engine", "demo")
    is_demo = bool(result.get("is_demo", True))
    quote_title = result.get("quote_title") or Path(fname).stem
    merged_sheet = dict(result["merged_sheet"])
    merged_sheet["source_filename"] = fname
    merged_sheet["export_filename"] = quote_title

    raw_sheets = []
    for rsh in (result.get("raw_sheets") or []):
        rsh_copy = dict(rsh)
        rsh_copy["source_filename"] = fname
        rsh_copy["export_filename"] = rsh_copy.get("name") or quote_title
        raw_sheets.append(rsh_copy)

    suggest_split = bool(result.get("suggest_split_sheets", False))
    use_multi_sheets = (not merge_per_image or suggest_split) and len(raw_sheets) > 1
    xlsx_bytes = build_xlsx_bytes(raw_sheets) if use_multi_sheets else build_single_xlsx_bytes(merged_sheet)
    safe_xlsx_name = f"{_safe_filename(quote_title, Path(fname).stem)}.xlsx"
    b2_xlsx_key = f"jobs/{job.job_uid}/{item_index + 1:02d}_{safe_xlsx_name}"

    try:
        stored_xlsx_ref = save_object(
            b2_xlsx_key,
            xlsx_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as exc:
        stored_xlsx_ref = ""
        result["notice"] = f"{result.get('notice', '')}（B2 上传告警：{exc}）"

    row_cnt = len(merged_sheet.get("rows") or [])
    col_cnt = len(merged_sheet.get("headers") or [])
    warn_cnt = len(merged_sheet.get("warnings") or [])

    quotation_obj = {
        "source_filename": fname,
        "quote_title": quote_title,
        "document_notes": result.get("document_notes", ""),
        "engine": used_engine,
        "is_demo": is_demo,
        "notice": result.get("notice", ""),
        "detected_doc_type": result.get("detected_doc_type", "quotation"),
        "suggest_split_sheets": suggest_split,
        "merged_sheet": merged_sheet,
        "raw_sheets": raw_sheets,
        "plain_text": result.get("plain_text", ""),
    }

    # 单张完成后即把轻量单图 JSON 存入 B2，彻底摆脱 finalize 时 19 张大 JSON 触发 Vercel 4.5MB 413 限制
    try:
        save_object(
            f"jobs/{job.job_uid}/items/{item_index + 1:02d}.json",
            json.dumps(quotation_obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    except Exception:
        pass

    # 若该 item_index 此前有失败占位记录（如点击「仅重试失败图片」补跑成功），先清除旧失败行避免重复计数
    old_items = session.exec(
        select(BatchJobItem).where(
            BatchJobItem.job_uid == job.job_uid,
            BatchJobItem.item_index == item_index,
        )
    ).all()
    for old_it in old_items:
        session.delete(old_it)
    session.flush()

    job_item = BatchJobItem(
        job_uid=job.job_uid,
        item_index=item_index,
        source_filename=fname[:120],
        quote_title=quote_title[:64],
        engine=used_engine,
        is_demo=is_demo,
        row_count=row_cnt,
        col_count=col_cnt,
        warning_count=warn_cnt,
        xlsx_b2_key=stored_xlsx_ref,
        xlsx_filename=safe_xlsx_name,
        status="completed",
        created_at=utc_now_str(),
    )
    session.add(job_item)

    record = ConversionRecord(
        filename=fname[:120],
        file_size_bytes=file_size_bytes,
        mode=mode,
        engine=used_engine,
        sheet_count=1 if merge_per_image else len(raw_sheets),
        row_count=row_cnt,
        col_count=col_cnt,
        warning_count=warn_cnt,
        status="demo" if is_demo else "success",
        created_at=utc_now_str(),
    )
    session.add(record)

    # 先落库 BatchJobItem，再基于真实行计数刷新 job.completed_items 与原子累加每日额度，彻底消除并发丢计数！
    session.flush()
    _reconcile_job_state(session, job)
    job.updated_at = utc_now_str()
    session.add(job)

    now_str = utc_now_str()
    session.exec(
        update(DailyQuota)
        .where(DailyQuota.id == quota.id)
        .values(used_count=DailyQuota.used_count + 1, updated_at=now_str)
    )
    session.commit()
    session.refresh(job_item)
    session.refresh(quota)

    return {
        "job_uid": job.job_uid,
        "item": job_item.model_dump(),
        "quotation": quotation_obj,
        "quota": _format_quota_payload(quota),
    }


def _ocr_error_status_code(err_msg: str) -> int:
    """避免返回 502 被 Cloudflare 劫持替换为英文 HTML 页面，确保中文错误明细完整透传到前端。"""
    s = str(err_msg or "")
    if any(k in s for k in ("429", "RPD", "RPM", "速率限制", "今日额度")):
        return 429
    return 503


@router.post("/extract-slice")
async def extract_single_slice(
    file: UploadFile = File(...),
    slice_index: int = Form(0),
    mode: str = Form("excel"),
    force_demo: bool = Form(False),
    custom_hint: str = Form(""),
    model_tier: str = Form("fast"),
    prices_only: bool = Form(False),
    doc_type: str = Form("auto"),
    dual_verify: bool = Form(True),
) -> Dict[str, Any]:
    """长图切片独立识别接口（默认开启双模型并发交叉核验，待全部切片完成后再统一计费与入库）。"""
    raw_bytes = await file.read()
    fname = _validate_upload_image(file, raw_bytes, slice_index)
    try:
        result = extract_tables_from_image(
            image_bytes=raw_bytes,
            filename=fname,
            content_type=file.content_type,
            mode=mode,
            force_demo=force_demo,
            custom_hint=custom_hint,
            merge_per_image=False,
            model_tier=model_tier,
            prices_only=prices_only,
            doc_type=doc_type,
            dual_verify=dual_verify,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=_ocr_error_status_code(str(exc)), detail=str(exc))
    return result


@router.post("/jobs/{job_uid}/commit-quotation")
def commit_sliced_quotation(
    job_uid: str,
    req: CommitQuotationRequest,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """将长图各分段识别结果无损拼接、重叠去重，并生成单份 .xlsx 归档至 Backblaze B2。"""
    job = session.exec(select(BatchJob).where(BatchJob.job_uid == job_uid)).first()
    if not job:
        raise HTTPException(status_code=404, detail="未找到对应的批处理任务")

    if req.failed_error and not req.slice_results:
        failed_item = BatchJobItem(
            job_uid=job_uid,
            item_index=req.item_index,
            source_filename=req.source_filename[:120],
            quote_title=Path(req.source_filename).stem[:48],
            status="failed",
            error_message=req.failed_error[:300],
            created_at=utc_now_str(),
        )
        session.add(failed_item)
        session.flush()
        _reconcile_job_state(session, job)
        job.updated_at = utc_now_str()
        session.add(job)
        session.commit()
        return {"job_uid": job_uid, "item": failed_item.model_dump()}

    quota = _get_or_create_today_quota(session)
    merged_result = merge_quotation_slices(
        req.slice_results,
        filename=req.source_filename,
        merge_per_image=req.merge_per_image,
        doc_type=req.doc_type,
    )
    # 零 AI Token 本地查表：仅对存在分歧/留空的机型或网络型号做唯一消歧，正常单元格绝不触碰
    apply_model_dict_to_result(session, merged_result)
    return _persist_completed_quotation(
        session=session,
        job=job,
        quota=quota,
        item_index=req.item_index,
        fname=req.source_filename,
        file_size_bytes=req.file_size_bytes,
        mode=req.mode,
        merge_per_image=req.merge_per_image,
        result=merged_result,
    )


@router.post("/jobs/{job_uid}/process-item")
async def process_batch_item(
    job_uid: str,
    file: UploadFile = File(...),
    item_index: int = Form(0),
    mode: str = Form("excel"),
    force_demo: bool = Form(False),
    custom_hint: str = Form(""),
    merge_per_image: bool = Form(True),
    model_tier: str = Form("fast"),
    prices_only: bool = Form(False),
    doc_type: str = Form("auto"),
    dual_verify: bool = Form(True),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    job = session.exec(select(BatchJob).where(BatchJob.job_uid == job_uid)).first()
    if not job:
        raise HTTPException(status_code=404, detail="未找到对应的批处理任务")

    quota = _get_or_create_today_quota(session)
    if not force_demo and has_gemini_key() and quota.daily_limit > 0 and (quota.used_count + 1 > quota.daily_limit):
        raise HTTPException(status_code=429, detail="今日额度已达上限，可在工作台点击「设置」调整今日额度")

    raw_bytes = await file.read()
    fname = _validate_upload_image(file, raw_bytes, item_index)

    try:
        result = extract_tables_from_image(
            image_bytes=raw_bytes,
            filename=fname,
            content_type=file.content_type,
            mode=mode,
            force_demo=force_demo,
            custom_hint=custom_hint,
            merge_per_image=merge_per_image,
            model_tier=model_tier,
            prices_only=prices_only,
            doc_type=doc_type,
            dual_verify=dual_verify,
        )
    except RuntimeError as exc:
        failed_item = BatchJobItem(
            job_uid=job_uid,
            item_index=item_index,
            source_filename=fname[:120],
            quote_title=Path(fname).stem[:48],
            status="failed",
            error_message=str(exc)[:300],
            created_at=utc_now_str(),
        )
        session.add(failed_item)
        session.flush()
        _reconcile_job_state(session, job)
        job.updated_at = utc_now_str()
        session.add(job)
        session.commit()
        raise HTTPException(status_code=_ocr_error_status_code(str(exc)), detail=str(exc))

    apply_model_dict_to_result(session, result)
    return _persist_completed_quotation(
        session=session,
        job=job,
        quota=quota,
        item_index=item_index,
        fname=fname,
        file_size_bytes=len(raw_bytes),
        mode=mode,
        merge_per_image=merge_per_image,
        result=result,
    )


@router.post("/jobs/{job_uid}/finalize")
def finalize_batch_job(
    job_uid: str,
    req: FinalizeJobRequest,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """
    轻量级批次收尾接口（支持 0 payload，不再要求前端上传几 MB 整批 JSON 撞 Vercel 4.5MB 413 上限）：
    - 以 BatchJobItem 真实行重新校准 completed_items / failed_items；
    - 直接从 B2 读取已存单表 .xlsx 打包整批 .zip 存入 B2；
    - 释放多设备运行锁并置终态。
    """
    job = session.exec(select(BatchJob).where(BatchJob.job_uid == job_uid)).first()
    if not job:
        raise HTTPException(status_code=404, detail="未找到对应的批处理任务")

    items = list(
        session.exec(
            select(BatchJobItem).where(BatchJobItem.job_uid == job_uid).order_by(BatchJobItem.item_index)
        ).all()
    )
    real_completed, real_failed, _ = _reconcile_job_state(session, job, items)
    completed_items_rows = [it for it in items if it.status == "completed" and it.xlsx_b2_key]

    quotations_dicts = [q.model_dump() for q in req.quotations]
    if quotations_dicts:
        json_bytes = json.dumps(
            {
                "job_uid": job_uid,
                "merge_per_image": req.merge_per_image,
                "output_mode": req.output_mode,
                "quotations": quotations_dicts,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            job.json_b2_key = save_object(
                f"jobs/{job_uid}/payload.json",
                json_bytes,
                "application/json; charset=utf-8",
            )
        except Exception:
            job.json_b2_key = "__items__" if real_completed > 0 else ""
    elif real_completed > 0:
        job.json_b2_key = "__items__"

    # 只要有 >=2 份已完成 .xlsx，优先直接从 B2 现有 .xlsx 打包 .zip（零额外大请求体！）
    if len(completed_items_rows) > 1:
        try:
            zip_bytes, zip_name = _build_zip_from_b2_items(job, completed_items_rows)
            if zip_bytes and zip_name:
                job.zip_b2_key = save_object(f"jobs/{job_uid}/{zip_name}", zip_bytes, "application/zip")
                job.zip_filename = zip_name
        except Exception:
            job.zip_b2_key = ""
    elif len(quotations_dicts) > 1:
        export_sheets: List[Dict[str, Any]] = []
        for q in quotations_dicts:
            sh = dict(q["merged_sheet"])
            sh["export_filename"] = q["quote_title"]
            sh["name"] = q["quote_title"][:28]
            export_sheets.append(sh)
        zip_bytes = build_batch_zip_bytes(export_sheets)
        zip_name = f"{_safe_filename(job.title, '批量表格打包')}_{job_uid[:6]}.zip"
        try:
            job.zip_b2_key = save_object(f"jobs/{job_uid}/{zip_name}", zip_bytes, "application/zip")
            job.zip_filename = zip_name
        except Exception:
            job.zip_b2_key = ""

    if real_completed == 0 and real_failed > 0:
        job.status = "failed"
    elif real_failed > 0 or (real_completed > 0 and real_completed < job.total_items):
        job.status = "partial_failed"
    elif real_completed > 0:
        job.status = "completed"
    else:
        job.status = "failed"

    job.merge_per_image = req.merge_per_image
    job.output_mode = req.output_mode
    job.storage_backend = "b2" if b2_is_enabled() else "local"
    job.updated_at = utc_now_str()
    _delete_client_lock(session, job_uid)
    session.add(job)
    session.commit()
    session.refresh(job)

    return {
        **job.model_dump(),
        "can_download_zip": bool(job.zip_b2_key) or len(completed_items_rows) >= 2,
        "completed_xlsx_count": len(completed_items_rows),
        "items": [it.model_dump() for it in items],
    }


@router.get("/jobs")
def list_batch_jobs(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    jobs = list(session.exec(select(BatchJob).order_by(desc(BatchJob.id)).limit(25)).all())
    if not jobs:
        return []
    job_uids = [j.job_uid for j in jobs]
    all_items = session.exec(
        select(BatchJobItem).where(BatchJobItem.job_uid.in_(job_uids)).order_by(BatchJobItem.item_index)
    ).all()
    items_by_uid: Dict[str, List[BatchJobItem]] = {}
    for it in all_items:
        items_by_uid.setdefault(it.job_uid, []).append(it)

    any_changed = False
    result_list: List[Dict[str, Any]] = []
    for j in jobs:
        j_items = items_by_uid.get(j.job_uid, [])
        _, _, changed = _reconcile_job_state(session, j, j_items)
        if changed:
            any_changed = True
        xlsx_cnt = sum(1 for it in j_items if it.status == "completed" and it.xlsx_b2_key)
        result_list.append(
            {
                **j.model_dump(),
                "can_download_zip": bool(j.zip_b2_key) or xlsx_cnt >= 2,
                "completed_xlsx_count": xlsx_cnt,
                "items": [it.model_dump() for it in j_items],
            }
        )

    if any_changed:
        session.commit()
    return result_list


@router.get("/jobs/{job_uid}/download")
def download_job_artifact(
    job_uid: str,
    item_id: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    job = session.exec(select(BatchJob).where(BatchJob.job_uid == job_uid)).first()
    if not job:
        raise HTTPException(status_code=404, detail="未找到对应的批处理任务")

    if item_id is not None:
        item = session.get(BatchJobItem, item_id)
        if not item or item.job_uid != job_uid:
            raise HTTPException(status_code=404, detail="未找到对应的单份表格文件")
        raw = load_object(item.xlsx_b2_key)
        if not raw:
            raise HTTPException(status_code=404, detail="对象存储中未找到该表格文件")
        fname = item.xlsx_filename or f"{_safe_filename(item.quote_title)}.xlsx"
        return StreamingResponse(
            io.BytesIO(raw),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=make_download_headers(fname),
        )

    # 无 item_id：有已缓存批量 .zip 优先下 .zip
    if job.zip_b2_key:
        raw = load_object(job.zip_b2_key)
        if raw:
            fname = job.zip_filename or f"{job.title}_打包.zip"
            return StreamingResponse(
                io.BytesIO(raw),
                media_type="application/zip",
                headers=make_download_headers(fname),
            )

    # 兜底按需现打 .zip：只要批次内有 >=2 份已完成 .xlsx（含历史遗留或中途中断批次），自动从 B2 打包下载并缓存！
    completed_items = list(
        session.exec(
            select(BatchJobItem)
            .where(BatchJobItem.job_uid == job_uid, BatchJobItem.status == "completed")
            .order_by(BatchJobItem.item_index)
        ).all()
    )
    completed_with_xlsx = [it for it in completed_items if it.xlsx_b2_key]
    if len(completed_with_xlsx) >= 2:
        zip_bytes, zip_name = _build_zip_from_b2_items(job, completed_with_xlsx)
        if zip_bytes and zip_name:
            try:
                job.zip_b2_key = save_object(f"jobs/{job_uid}/{zip_name}", zip_bytes, "application/zip")
                job.zip_filename = zip_name
                _reconcile_job_state(session, job)
                session.add(job)
                session.commit()
            except Exception:
                pass
            return StreamingResponse(
                io.BytesIO(zip_bytes),
                media_type="application/zip",
                headers=make_download_headers(zip_name),
            )

    first_item = completed_with_xlsx[0] if completed_with_xlsx else None
    if not first_item or not first_item.xlsx_b2_key:
        raise HTTPException(status_code=404, detail="该批次暂无可下载产物")
    raw = load_object(first_item.xlsx_b2_key)
    if not raw:
        raise HTTPException(status_code=404, detail="对象存储中未找到该表格文件")
    fname = first_item.xlsx_filename or f"{_safe_filename(first_item.quote_title)}.xlsx"
    return StreamingResponse(
        io.BytesIO(raw),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=make_download_headers(fname),
    )


@router.get("/jobs/{job_uid}/load")
def load_job_payload(job_uid: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    job = session.exec(select(BatchJob).where(BatchJob.job_uid == job_uid)).first()
    if not job:
        raise HTTPException(status_code=404, detail="未找到对应的批处理任务")
    if not job.json_b2_key:
        raise HTTPException(status_code=404, detail="该批次未保存可二次编辑的结构化数据")

    if job.json_b2_key == "__items__":
        items = list(
            session.exec(
                select(BatchJobItem)
                .where(BatchJobItem.job_uid == job_uid, BatchJobItem.status == "completed")
                .order_by(BatchJobItem.item_index)
            ).all()
        )
        quotations: List[Dict[str, Any]] = []
        for it in items:
            rel_key = f"jobs/{job_uid}/items/{it.item_index + 1:02d}.json"
            if it.xlsx_b2_key and it.xlsx_b2_key.startswith("b2://"):
                b_name = it.xlsx_b2_key[len("b2://") :].split("/", 1)[0]
                item_ref = f"b2://{b_name}/{rel_key}"
            elif b2_is_enabled():
                item_ref = f"b2://{get_b2_bucket_name()}/{rel_key}"
            else:
                item_ref = rel_key
            raw_it = load_object(item_ref)
            if raw_it:
                try:
                    quotations.append(json.loads(raw_it.decode("utf-8")))
                except Exception:
                    continue
        if not quotations:
            raise HTTPException(status_code=404, detail="对象存储中未找到该批次的结构化编辑数据")
        return {
            "job_uid": job_uid,
            "merge_per_image": job.merge_per_image,
            "output_mode": job.output_mode,
            "quotations": quotations,
        }

    raw = load_object(job.json_b2_key)
    if not raw:
        raise HTTPException(status_code=404, detail="从 Backblaze 对象存储读取结构化数据失败")
    return json.loads(raw.decode("utf-8"))


@router.delete("/jobs/{job_uid}")
def delete_batch_job(job_uid: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    job = session.exec(select(BatchJob).where(BatchJob.job_uid == job_uid)).first()
    if not job:
        raise HTTPException(status_code=404, detail="未找到对应的批处理任务")

    storage_report = {"deleted_files": 0, "freed_bytes": 0}
    storage_warning = ""
    try:
        storage_report = delete_prefix_objects(f"jobs/{job_uid}/")
    except Exception as exc:
        storage_warning = str(exc)

    items = session.exec(select(BatchJobItem).where(BatchJobItem.job_uid == job_uid)).all()
    for it in items:
        session.delete(it)
    _delete_client_lock(session, job_uid)
    session.delete(job)
    session.commit()
    return {
        "deleted": True,
        "job_uid": job_uid,
        "deleted_files": storage_report["deleted_files"],
        "freed_bytes": storage_report["freed_bytes"],
        "storage_warning": storage_warning,
    }


@router.delete("/records/all")
def clear_all_records(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """一键清空全部批处理记录、单张调用明细，并同步物理删除 Backblaze B2 中的全部存档文件及历史版本。"""
    storage_report = {"deleted_files": 0, "freed_bytes": 0}
    storage_warning = ""
    try:
        storage_report = purge_bucket_files()
    except Exception as exc:
        storage_warning = str(exc)

    items = session.exec(select(BatchJobItem)).all()
    jobs = session.exec(select(BatchJob)).all()
    records = session.exec(select(ConversionRecord)).all()

    deleted_jobs = len(jobs)
    deleted_records = len(records)

    for it in items:
        session.delete(it)
    for j in jobs:
        session.delete(j)
    for r in records:
        session.delete(r)
    session.commit()

    return {
        "cleared": True,
        "deleted_jobs": deleted_jobs,
        "deleted_records": deleted_records,
        "deleted_files": storage_report["deleted_files"],
        "freed_bytes": storage_report["freed_bytes"],
        "storage_warning": storage_warning,
    }


@router.get("/storage/stats")
def storage_stats() -> Dict[str, Any]:
    """查询对象存储当前占用文件数与体积，便于随时监控免费空间。"""
    try:
        return get_storage_stats()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"查询对象存储空间失败：{exc}")


@router.post("/storage/purge-b2")
def purge_b2_storage(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """一键物理清空 Backblaze B2 桶内所有残留对象与历史隐藏版本，彻底释放 10GB 免费额度。"""
    try:
        report = purge_bucket_files()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"清理 Backblaze B2 存储桶失败：{exc}")

    # 同步置空历史记录中已失效的对象键引用，避免点击下载报 404
    jobs = session.exec(select(BatchJob)).all()
    for j in jobs:
        if j.zip_b2_key or j.json_b2_key:
            j.zip_b2_key = ""
            j.json_b2_key = ""
            session.add(j)
    items = session.exec(select(BatchJobItem)).all()
    for it in items:
        if it.xlsx_b2_key:
            it.xlsx_b2_key = ""
            session.add(it)
    session.commit()

    return {
        "purged": True,
        "deleted_files": report["deleted_files"],
        "freed_bytes": report["freed_bytes"],
    }


@router.post("/extract")
async def extract_tables(
    files: List[UploadFile] = File(...),
    mode: str = Form("excel"),
    output_mode: str = Form("per_image"),
    force_demo: bool = Form(False),
    custom_hint: str = Form(""),
    merge_per_image: bool = Form(True),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """兼容单次直接提取接口（同步支持单图合表与 raw_sheets 返回）。"""
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传 1 张表格图片")
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="单次最多支持批量上传 10 张图片")

    quota = _get_or_create_today_quota(session)
    if not force_demo and has_gemini_key() and quota.daily_limit > 0 and (quota.used_count + len(files) > quota.daily_limit):
        raise HTTPException(status_code=429, detail="今日额度已达上限，可在工作台点击「设置」调整今日额度")

    all_sheets: List[Dict[str, Any]] = []
    quotations: List[Dict[str, Any]] = []
    plain_texts: List[str] = []
    notices: List[str] = []
    used_engine = "demo"
    is_demo = True

    for idx, up_file in enumerate(files):
        raw_bytes = await up_file.read()
        fname = _validate_upload_image(up_file, raw_bytes, idx)

        try:
            result = extract_tables_from_image(
                image_bytes=raw_bytes,
                filename=fname,
                content_type=up_file.content_type,
                mode=mode,
                force_demo=force_demo,
                custom_hint=custom_hint,
                merge_per_image=merge_per_image,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=_ocr_error_status_code(str(exc)), detail=str(exc))

        apply_model_dict_to_result(session, result)
        used_engine = result.get("engine", "demo")
        is_demo = bool(result.get("is_demo", True))
        if result.get("notice"):
            notices.append(result["notice"])
        if result.get("plain_text"):
            plain_texts.append(f"### {fname}\n\n{result['plain_text']}")

        quote_title = result.get("quote_title") or Path(fname).stem
        merged_sheet = dict(result["merged_sheet"])
        merged_sheet["source_filename"] = fname
        merged_sheet["export_filename"] = quote_title

        raw_sheets = []
        for rsh in (result.get("raw_sheets") or []):
            rsh_copy = dict(rsh)
            rsh_copy["source_filename"] = fname
            rsh_copy["export_filename"] = rsh_copy.get("name") or quote_title
            raw_sheets.append(rsh_copy)

        quotations.append(
            {
                "source_filename": fname,
                "quote_title": quote_title,
                "document_notes": result.get("document_notes", ""),
                "engine": used_engine,
                "is_demo": is_demo,
                "notice": result.get("notice", ""),
                "merged_sheet": merged_sheet,
                "raw_sheets": raw_sheets,
                "plain_text": result.get("plain_text", ""),
            }
        )

        active_sheets = [merged_sheet] if merge_per_image else raw_sheets
        all_sheets.extend(active_sheets)

        total_rows = sum(len(s.get("rows") or []) for s in active_sheets)
        max_cols = max((len(s.get("headers") or []) for s in active_sheets), default=0)
        total_warnings = sum(len(s.get("warnings") or []) for s in active_sheets)
        record = ConversionRecord(
            filename=fname[:120],
            file_size_bytes=len(raw_bytes),
            mode=mode,
            engine=used_engine,
            sheet_count=len(active_sheets),
            row_count=total_rows,
            col_count=max_cols,
            warning_count=total_warnings,
            status="demo" if is_demo else "success",
            created_at=utc_now_str(),
        )
        session.add(record)

    quota.used_count += len(files)
    quota.updated_at = utc_now_str()
    session.add(quota)
    session.commit()

    return {
        "title": quotations[0]["quote_title"] if len(quotations) == 1 else f"批量提取 ({len(files)} 份报价单)",
        "mode": mode,
        "output_mode": output_mode,
        "engine": used_engine,
        "is_demo": is_demo,
        "notice": notices[0] if notices else "",
        "sheets": all_sheets,
        "quotations": quotations,
        "plain_text": "\n\n---\n\n".join(plain_texts),
        "quota": _format_quota_payload(quota),
    }


@router.post("/export")
def export_tables(req: ExportRequest) -> StreamingResponse:
    sheets_dicts = [s.model_dump() for s in req.sheets]
    file_bytes, dl_filename, media_type = export_tables_package(
        sheets=sheets_dicts,
        output_mode=req.output_mode,
        file_format=req.format,
        base_filename=req.filename,
    )
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=media_type,
        headers=make_download_headers(dl_filename),
    )

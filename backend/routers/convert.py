import io
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from database import DB_MODE, get_session
from models import BatchJob, BatchJobItem, ConversionRecord, DailyQuota, utc_now_str, utc_today_str
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
    export_tables_package,
    make_download_headers,
)
from services.ocr_service import (
    extract_tables_from_image,
    get_active_model,
    has_gemini_key,
    merge_quotation_slices,
)

router = APIRouter(prefix="/api", tags=["convert"], dependencies=[Depends(require_auth)])

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".heic", ".heif"}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_DAILY_LIMIT = 50


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
            daily_limit=DEFAULT_DAILY_LIMIT,
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
    remaining = max(0, quota.daily_limit - quota.used_count)
    return {
        "quota_date": quota.quota_date,
        "used_count": quota.used_count,
        "daily_limit": quota.daily_limit,
        "remaining": remaining,
        "gemini_configured": has_gemini_key(),
        "model": get_active_model(),
        "b2_configured": b2_is_enabled(),
    }


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
        raise HTTPException(status_code=400, detail="单批次支持 1~20 张报价单图片")

    job_uid = uuid.uuid4().hex[:12]
    job = BatchJob(
        job_uid=job_uid,
        title=_safe_filename(req.title, "批量报价单任务"),
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
    session.commit()
    session.refresh(job)
    return job.model_dump()


class CommitQuotationRequest(BaseModel):
    item_index: int = 0
    source_filename: str
    file_size_bytes: int = 0
    mode: str = "excel"
    merge_per_image: bool = True
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

    target_sheet = merged_sheet if merge_per_image else (raw_sheets[0] if raw_sheets else merged_sheet)
    xlsx_bytes = build_single_xlsx_bytes(target_sheet)
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

    job.completed_items += 1
    job.updated_at = utc_now_str()
    session.add(job)

    quota.used_count += 1
    quota.updated_at = utc_now_str()
    session.add(quota)
    session.commit()
    session.refresh(job_item)

    return {
        "job_uid": job.job_uid,
        "item": job_item.model_dump(),
        "quotation": {
            "source_filename": fname,
            "quote_title": quote_title,
            "document_notes": result.get("document_notes", ""),
            "engine": used_engine,
            "is_demo": is_demo,
            "notice": result.get("notice", ""),
            "merged_sheet": merged_sheet,
            "raw_sheets": raw_sheets,
            "plain_text": result.get("plain_text", ""),
        },
        "quota": {
            "quota_date": quota.quota_date,
            "used_count": quota.used_count,
            "daily_limit": quota.daily_limit,
            "remaining": max(0, quota.daily_limit - quota.used_count),
        },
    }


@router.post("/extract-slice")
async def extract_single_slice(
    file: UploadFile = File(...),
    slice_index: int = Form(0),
    mode: str = Form("excel"),
    force_demo: bool = Form(False),
    custom_hint: str = Form(""),
    model_tier: str = Form("fast"),
    prices_only: bool = Form(False),
) -> Dict[str, Any]:
    """长图切片独立识别接口（仅返回结构化切片结果，待全部切片完成后再统一计费与入库）。"""
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
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
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
        job.failed_items += 1
        job.updated_at = utc_now_str()
        session.add(failed_item)
        session.add(job)
        session.commit()
        return {"job_uid": job_uid, "item": failed_item.model_dump()}

    quota = _get_or_create_today_quota(session)
    merged_result = merge_quotation_slices(
        req.slice_results,
        filename=req.source_filename,
        merge_per_image=req.merge_per_image,
    )
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
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    job = session.exec(select(BatchJob).where(BatchJob.job_uid == job_uid)).first()
    if not job:
        raise HTTPException(status_code=404, detail="未找到对应的批处理任务")

    quota = _get_or_create_today_quota(session)
    if not force_demo and has_gemini_key() and (quota.used_count + 1 > quota.daily_limit):
        raise HTTPException(status_code=429, detail="今日免费转换次数已达上限，请明日再试或开启演示模式")

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
        job.failed_items += 1
        job.updated_at = utc_now_str()
        session.add(failed_item)
        session.add(job)
        session.commit()
        raise HTTPException(status_code=502, detail=str(exc))

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
    job = session.exec(select(BatchJob).where(BatchJob.job_uid == job_uid)).first()
    if not job:
        raise HTTPException(status_code=404, detail="未找到对应的批处理任务")

    quotations_dicts = [q.model_dump() for q in req.quotations]
    # 存储结构化 JSON 供「转换记录 -> 载入工作台继续编辑」（B2 瞬时抖动容错，不影响已完成的提取结果）
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
            job.json_b2_key = ""

        # 若包含多份报价单，额外生成一份打包 .zip 存入 Backblaze B2
        if len(quotations_dicts) > 1:
            export_sheets: List[Dict[str, Any]] = []
            for q in quotations_dicts:
                sh = dict(q["merged_sheet"])
                sh["export_filename"] = q["quote_title"]
                sh["name"] = q["quote_title"][:28]
                export_sheets.append(sh)
            zip_bytes = build_batch_zip_bytes(export_sheets)
            zip_name = f"{_safe_filename(job.title, '报价单批量打包')}_{job_uid[:6]}.zip"
            try:
                job.zip_b2_key = save_object(f"jobs/{job_uid}/{zip_name}", zip_bytes, "application/zip")
                job.zip_filename = zip_name
            except Exception:
                job.zip_b2_key = ""

    if job.completed_items == 0 and job.failed_items > 0:
        job.status = "failed"
    elif job.failed_items > 0:
        job.status = "partial_failed"
    else:
        job.status = "completed"

    job.storage_backend = "b2" if b2_is_enabled() else "local"
    job.updated_at = utc_now_str()
    session.add(job)
    session.commit()
    session.refresh(job)

    items = session.exec(
        select(BatchJobItem).where(BatchJobItem.job_uid == job_uid).order_by(BatchJobItem.item_index)
    ).all()
    return {
        **job.model_dump(),
        "items": [it.model_dump() for it in items],
    }


@router.get("/jobs")
def list_batch_jobs(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    jobs = session.exec(select(BatchJob).order_by(desc(BatchJob.id)).limit(25)).all()
    if not jobs:
        return []
    job_uids = [j.job_uid for j in jobs]
    all_items = session.exec(
        select(BatchJobItem).where(BatchJobItem.job_uid.in_(job_uids)).order_by(BatchJobItem.item_index)
    ).all()
    items_by_uid: Dict[str, List[Dict[str, Any]]] = {}
    for it in all_items:
        items_by_uid.setdefault(it.job_uid, []).append(it.model_dump())
    return [{**j.model_dump(), "items": items_by_uid.get(j.job_uid, [])} for j in jobs]


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
            raise HTTPException(status_code=404, detail="未找到对应的单份报价单文件")
        raw = load_object(item.xlsx_b2_key)
        if not raw:
            raise HTTPException(status_code=404, detail="对象存储中未找到该报价单文件")
        fname = item.xlsx_filename or f"{_safe_filename(item.quote_title)}.xlsx"
        return StreamingResponse(
            io.BytesIO(raw),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=make_download_headers(fname),
        )

    # 无 item_id：有批量 .zip 优先下 .zip，单图任务下唯一的 .xlsx
    if job.zip_b2_key:
        raw = load_object(job.zip_b2_key)
        if raw:
            fname = job.zip_filename or f"{job.title}_打包.zip"
            return StreamingResponse(
                io.BytesIO(raw),
                media_type="application/zip",
                headers=make_download_headers(fname),
            )

    first_item = session.exec(
        select(BatchJobItem)
        .where(BatchJobItem.job_uid == job_uid, BatchJobItem.status == "completed")
        .order_by(BatchJobItem.item_index)
    ).first()
    if not first_item or not first_item.xlsx_b2_key:
        raise HTTPException(status_code=404, detail="该批次暂无可下载产物")
    raw = load_object(first_item.xlsx_b2_key)
    if not raw:
        raise HTTPException(status_code=404, detail="对象存储中未找到该报价单文件")
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
    if not force_demo and has_gemini_key() and (quota.used_count + len(files) > quota.daily_limit):
        raise HTTPException(status_code=429, detail="今日免费转换次数已达上限，请明日再试或使用演示模式预览")

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
            raise HTTPException(status_code=502, detail=str(exc))

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
        "quota": {
            "quota_date": quota.quota_date,
            "used_count": quota.used_count,
            "daily_limit": quota.daily_limit,
            "remaining": max(0, quota.daily_limit - quota.used_count),
        },
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

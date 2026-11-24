import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from database import DB_MODE, get_session
from models import ConversionRecord, DailyQuota, utc_now_str, utc_today_str
from services.excel_exporter import export_tables_package, make_download_headers
from services.ocr_service import (
    extract_tables_from_image,
    get_active_model,
    has_gemini_key,
)

router = APIRouter(prefix="/api", tags=["convert"])

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".heic", ".heif"}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_DAILY_LIMIT = 50


class SheetPayload(BaseModel):
    name: str = "Sheet1"
    headers: List[str]
    col_types: List[str] = []
    rows: List[List[str]] = []
    merges: List[Dict[str, int]] = []
    source_filename: Optional[str] = None


class ExportRequest(BaseModel):
    sheets: List[SheetPayload]
    output_mode: str = "all_in_one"  # all_in_one | per_image
    format: str = "xlsx"  # xlsx | csv
    filename: str = "image-to-excel"


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


@router.get("/health")
def health_check() -> Dict[str, Any]:
    return {
        "status": "ok",
        "db_mode": DB_MODE,
        "gemini_configured": has_gemini_key(),
        "model": get_active_model(),
    }


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


@router.post("/extract")
async def extract_tables(
    files: List[UploadFile] = File(...),
    mode: str = Form("excel"),
    output_mode: str = Form("all_in_one"),
    force_demo: bool = Form(False),
    custom_hint: str = Form(""),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传 1 张表格图片")
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="单次最多支持批量上传 10 张图片")

    quota = _get_or_create_today_quota(session)
    if not force_demo and has_gemini_key() and (quota.used_count + len(files) > quota.daily_limit):
        raise HTTPException(status_code=429, detail="今日免费转换次数已达上限，请明日再试或使用演示模式预览")

    all_sheets: List[Dict[str, Any]] = []
    plain_texts: List[str] = []
    notices: List[str] = []
    used_engine = "demo"
    is_demo = True

    for idx, up_file in enumerate(files):
        fname = up_file.filename or f"image_{idx + 1}.png"
        ext = Path(fname).suffix.lower()
        if ext and ext not in ALLOWED_EXTENSIONS and not (up_file.content_type or "").startswith("image/"):
            raise HTTPException(status_code=400, detail=f"不支持的文件格式：{fname}（仅支持 JPG/PNG/GIF/WEBP/HEIC）")

        raw_bytes = await up_file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail=f"文件内容为空：{fname}")
        if len(raw_bytes) > MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail=f"图片 {fname} 超过 10MB 上限，请压缩后重试")

        try:
            result = extract_tables_from_image(
                image_bytes=raw_bytes,
                filename=fname,
                content_type=up_file.content_type,
                mode=mode,
                force_demo=force_demo,
                custom_hint=custom_hint,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        used_engine = result.get("engine", "demo")
        is_demo = bool(result.get("is_demo", True))
        if result.get("notice"):
            notices.append(result["notice"])
        if result.get("plain_text"):
            plain_texts.append(f"### {fname}\n\n{result['plain_text']}")

        sheets = result.get("sheets") or []
        total_rows = sum(len(s.get("rows") or []) for s in sheets)
        max_cols = max((len(s.get("headers") or []) for s in sheets), default=0)
        total_warnings = sum(len(s.get("warnings") or []) for s in sheets)

        for s_idx, sh in enumerate(sheets):
            sh_copy = dict(sh)
            sh_copy["source_filename"] = fname
            sh_copy["source_index"] = idx
            if len(files) > 1 and not sh_copy.get("name"):
                sh_copy["name"] = f"{Path(fname).stem[:18]}_{s_idx + 1}"
            all_sheets.append(sh_copy)

        record = ConversionRecord(
            filename=fname[:120],
            file_size_bytes=len(raw_bytes),
            mode=mode,
            engine=used_engine,
            sheet_count=len(sheets),
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
        "title": all_sheets[0]["name"] if len(all_sheets) == 1 else f"批量提取 ({len(files)} 张图片)",
        "mode": mode,
        "output_mode": output_mode,
        "engine": used_engine,
        "is_demo": is_demo,
        "notice": notices[0] if notices else "",
        "sheets": all_sheets,
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

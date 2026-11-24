from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now_str() -> str:
    """统一返回 UTC 文本时间，规避 SQLite/Turso NaiveDatetime 校验异常。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def utc_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class ConversionRecord(SQLModel, table=True):
    """图片转表格轻量转换记录（严禁存储图片 BLOB，严守 Turso 5GB 配额红线）。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str = Field(index=True)
    file_size_bytes: int = Field(default=0)
    mode: str = Field(default="excel")
    engine: str = Field(default="demo")
    sheet_count: int = Field(default=1)
    row_count: int = Field(default=0)
    col_count: int = Field(default=0)
    warning_count: int = Field(default=0)
    status: str = Field(default="success")
    created_at: Optional[str] = Field(default_factory=utc_now_str)


class DailyQuota(SQLModel, table=True):
    """每日转换额度计数表。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    quota_date: str = Field(index=True)
    client_key: str = Field(default="default", index=True)
    used_count: int = Field(default=0)
    daily_limit: int = Field(default=50)
    updated_at: Optional[str] = Field(default_factory=utc_now_str)


class BatchJob(SQLModel, table=True):
    """批量报价单转换任务批次（产物文件存 Backblaze B2，库内仅存对象索引键）。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    job_uid: str = Field(index=True)
    title: str = Field(default="批量报价单任务")
    mode: str = Field(default="excel")
    output_mode: str = Field(default="per_image")
    merge_per_image: bool = Field(default=True)
    total_items: int = Field(default=0)
    completed_items: int = Field(default=0)
    failed_items: int = Field(default=0)
    status: str = Field(default="running")  # running | completed | partial_failed | failed
    zip_b2_key: str = Field(default="")
    zip_filename: str = Field(default="")
    json_b2_key: str = Field(default="")
    storage_backend: str = Field(default="local")  # b2 | local
    created_at: Optional[str] = Field(default_factory=utc_now_str)
    updated_at: Optional[str] = Field(default_factory=utc_now_str)


class BatchJobItem(SQLModel, table=True):
    """批次内单张报价单图片的处理状态与独立 .xlsx 存储索引。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    job_uid: str = Field(index=True)
    item_index: int = Field(default=0)
    source_filename: str = Field(default="")
    quote_title: str = Field(default="")
    engine: str = Field(default="demo")
    is_demo: bool = Field(default=False)
    row_count: int = Field(default=0)
    col_count: int = Field(default=0)
    warning_count: int = Field(default=0)
    xlsx_b2_key: str = Field(default="")
    xlsx_filename: str = Field(default="")
    status: str = Field(default="pending")  # completed | failed
    error_message: str = Field(default="")
    created_at: Optional[str] = Field(default_factory=utc_now_str)

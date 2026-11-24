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
    mode: str = Field(default="excel")  # excel | text
    engine: str = Field(default="demo")  # gemini-4-hydrogen | demo
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
    daily_limit: int = Field(default=20)
    updated_at: Optional[str] = Field(default_factory=utc_now_str)

import base64
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple

import requests
from sqlalchemy import event, insert as sa_insert, text
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

BACKEND_DIR = Path(__file__).resolve().parent
BASE_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    load_dotenv(BACKEND_DIR / ".env")
except Exception:
    pass

IS_VERCEL = bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))
STORAGE_DIR = Path("/tmp/storage") if IS_VERCEL else (BASE_DIR / "storage")
try:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    STORAGE_DIR = Path("/tmp/storage")
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

_JWT_RE = re.compile(r"(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")
_MULTI_INSERT_RE = re.compile(
    r"^\s*(INSERT\s+(?:OR\s+\w+\s+)?INTO\s+[^\(\s]+\s*\([^\)]+\)\s*VALUES\s*)\(([^\)]+)\)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _strip_clean(v: str) -> str:
    return (v or "").strip().strip("`'\"").strip()


def _get_clean_env(key_name: str) -> str:
    """全局唯一环境变量提纯函数：兼容变量名或值误带反引号、引号、空格的情况。"""
    if key_name in os.environ:
        val = _strip_clean(os.environ[key_name])
        if val:
            return val
    for k, v in os.environ.items():
        if _strip_clean(k) == key_name:
            return _strip_clean(v)
    return ""


def _normalize_turso_host(raw_url: str) -> str:
    u = _strip_clean(raw_url)
    for prefix in ("sqlite+libsql://", "libsql://", "turso://", "wss://", "ws://", "https://", "http://"):
        if u.lower().startswith(prefix):
            u = u[len(prefix):]
            break
    if "?" in u:
        u = u.split("?", 1)[0]
    for suffix in ("/v2/pipeline", "/v2", "/"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
    return u.strip("/")


def _extract_clean_jwt(raw_token: str) -> str:
    t = _strip_clean(raw_token)
    m = _JWT_RE.search(t)
    return m.group(1) if m else t


def expand_multi_insert(sql: str, rows: List[Any], max_rows: int = 120) -> List[Tuple[str, List[Any]]]:
    """
    将 executemany 的单行 INSERT 展开为多值 VALUES (...), (...)，
    每块行数严格遵循 max(1, min(120, 900 // cols_per_row))，兼容 SQLite 999 绑定参数上限。
    """
    m = _MULTI_INSERT_RE.match(sql.strip())
    if not m or not rows or isinstance(rows[0], dict):
        return [(sql, list(r) if not isinstance(r, dict) else r) for r in rows]
    prefix_sql = m.group(1)
    placeholders_one = f"({m.group(2).strip()})"
    cols_per_row = max(1, placeholders_one.count("?") or len(rows[0]))
    rows_per_stmt = max(1, min(max_rows, 900 // cols_per_row))
    out: List[Tuple[str, List[Any]]] = []
    for i in range(0, len(rows), rows_per_stmt):
        chunk = rows[i : i + rows_per_stmt]
        multi_sql = prefix_sql + ", ".join([placeholders_one] * len(chunk))
        flat_args = [p for row_params in chunk for p in row_params]
        out.append((multi_sql, flat_args))
    return out


class TursoHttpCursor:
    """PEP 249 兼容游标：支持 900 参数上限多行 VALUES 自动合并、单条真实 lastrowid 与事务写缓冲。"""

    def __init__(self, conn: "TursoHttpConnection") -> None:
        self.conn = conn
        self.description: Optional[Tuple[Tuple[Any, ...], ...]] = None
        self.rowcount: int = -1
        self.lastrowid: Optional[int] = None
        self._rows: List[Tuple[Any, ...]] = []
        self._pos: int = 0
        self.arraysize: int = 1

    @staticmethod
    def _encode_arg(val: Any) -> Dict[str, Any]:
        if val is None:
            return {"type": "null"}
        if isinstance(val, bool):
            return {"type": "integer", "value": "1" if val else "0"}
        if isinstance(val, int):
            return {"type": "integer", "value": str(val)}
        if isinstance(val, float):
            return {"type": "float", "value": float(val)}
        if isinstance(val, datetime):
            return {"type": "text", "value": val.strftime("%Y-%m-%d %H:%M:%S")}
        if isinstance(val, date):
            return {"type": "text", "value": val.isoformat()}
        if isinstance(val, (bytes, bytearray, memoryview)):
            return {"type": "blob", "base64": base64.b64encode(bytes(val)).decode("ascii")}
        return {"type": "text", "value": str(val)}

    @staticmethod
    def _decode_cell(cell: Dict[str, Any]) -> Any:
        t = cell.get("type")
        if t == "null" or t is None:
            return None
        if t == "integer":
            return int(cell["value"])
        if t == "float":
            return float(cell["value"])
        if t == "blob":
            return base64.b64decode(cell.get("base64", ""))
        return str(cell.get("value", ""))

    def _build_stmt(self, sql: str, parameters: Any = None) -> Dict[str, Any]:
        stmt: Dict[str, Any] = {"sql": sql}
        if parameters:
            if isinstance(parameters, dict):
                stmt["named_args"] = [{"name": k, "value": self._encode_arg(v)} for k, v in parameters.items()]
            else:
                stmt["args"] = [self._encode_arg(p) for p in parameters]
        return stmt

    def execute(self, sql: str, parameters: Any = None) -> "TursoHttpCursor":
        sql_upper = sql.strip().upper()
        if any(sql_upper.startswith(p) for p in (
            "PRAGMA JOURNAL_MODE", "PRAGMA SYNCHRONOUS", "PRAGMA CACHE_SIZE",
            "PRAGMA BUSY_TIMEOUT", "BEGIN", "COMMIT", "ROLLBACK"
        )):
            self.description = None
            self.rowcount = 0
            self._rows = []
            self._pos = 0
            return self

        stmt = self._build_stmt(sql, parameters)
        no_ret = "RETURNING " not in sql_upper
        if no_ret and sql_upper.startswith(("DELETE ", "UPDATE ")):
            self.conn._pending_writes.append({"type": "execute", "stmt": stmt})
            self.description = None
            self._rows = []
            self._pos = 0
            self.rowcount = 1
            return self

        if no_ret and sql_upper.startswith("INSERT "):
            # 单条 INSERT 必须拿到服务端真实 last_insert_rowid（供 ORM session.refresh 使用）
            batch = (
                [{"type": "execute", "stmt": {"sql": "BEGIN"}}]
                + self.conn._pending_writes
                + [{"type": "execute", "stmt": stmt}, {"type": "execute", "stmt": {"sql": "COMMIT"}}]
            )
            self.conn._pending_writes = []
            all_res = self.conn._send_pipeline(batch)
            res = all_res[-2] if len(all_res) >= 2 else {}
            self.description = None
            self._rows = []
            self._pos = 0
            self.rowcount = int(res.get("affected_row_count") or 0)
            last_id = res.get("last_insert_rowid")
            self.lastrowid = int(last_id) if (last_id is not None and str(last_id) != "") else None
            return self

        if self.conn._pending_writes:
            batch_reqs = (
                [{"type": "execute", "stmt": {"sql": "BEGIN"}}]
                + self.conn._pending_writes
                + [{"type": "execute", "stmt": {"sql": "COMMIT"}}, {"type": "execute", "stmt": stmt}]
            )
            self.conn._pending_writes = []
            all_res = self.conn._send_pipeline(batch_reqs)
            res = all_res[-1] if all_res else {}
        else:
            res = self.conn._send_pipeline([{"type": "execute", "stmt": stmt}])[0]

        cols = res.get("cols") or []
        self.description = tuple((c.get("name", ""), None, None, None, None, None, None) for c in cols) if cols else None
        self._rows = [tuple(self._decode_cell(c) for c in r) for r in (res.get("rows") or [])]
        self._pos = 0
        self.rowcount = int(res.get("affected_row_count") or 0)
        last_id = res.get("last_insert_rowid")
        self.lastrowid = int(last_id) if (last_id is not None and str(last_id) != "") else None
        return self

    def executemany(self, sql: str, seq_of_parameters: Iterable[Any]) -> "TursoHttpCursor":
        params_list = list(seq_of_parameters)
        if not params_list:
            return self
        for chunk_sql, chunk_args in expand_multi_insert(sql, params_list, max_rows=120):
            self.conn._pending_writes.append({"type": "execute", "stmt": self._build_stmt(chunk_sql, chunk_args)})
        self.rowcount = len(params_list)
        return self

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self):
        rows = self._rows[self._pos :]
        self._pos = len(self._rows)
        return rows

    def fetchmany(self, size: Optional[int] = None):
        sz = size if size is not None else self.arraysize
        rows = self._rows[self._pos : self._pos + sz]
        self._pos += len(rows)
        return rows

    def close(self) -> None:
        self._rows = []
        self._pos = 0


class TursoHttpConnection:
    def __init__(self, pipeline_url: str, auth_token: str, http_session: requests.Session) -> None:
        self.pipeline_url = pipeline_url
        self.auth_token = auth_token
        self.http_session = http_session
        self.isolation_level = None
        self._pending_writes: List[Dict[str, Any]] = []

    def _send_pipeline(self, execute_requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        payload = {"requests": execute_requests + [{"type": "close"}]}
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self.http_session.post(self.pipeline_url, json=payload, headers=headers, timeout=45)
            except requests.RequestException as exc:
                last_exc = exc
                # 清理已失效的 keep-alive 空闲连接，立即重建新连接重试（攻克 RemoteDisconnected）
                try:
                    self.http_session.close()
                except Exception:
                    pass
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise sqlite3.OperationalError(f"Turso 网络连接闪断，重试 3 次仍失败：{exc}") from exc

            if resp.status_code in (429, 502, 503, 504) and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            if resp.status_code != 200:
                err_body = resp.text[:300]
                if "JWT" in err_body or resp.status_code in (400, 401, 403):
                    token_state = ("已填写且形如 JWT" if self.auth_token.startswith("eyJ") else "已填写但非 eyJ 开头") if self.auth_token else "为空"
                    raise sqlite3.OperationalError(
                        f"Turso 认证令牌 (TURSO_AUTH_TOKEN) 校验未通过 (当前状态: {token_state}，长度 {len(self.auth_token)})。"
                        f"请确认填入的是以 eyJ 开头的 Token（而非 libsql:// 数据库地址），修改后需点击 Redeploy！"
                    )
                raise sqlite3.OperationalError(f"Turso 响应异常 (HTTP {resp.status_code}): {err_body}")

            data = resp.json()
            out: List[Dict[str, Any]] = []
            transient_err = ""
            for item in data.get("results", []):
                if item.get("type") == "error":
                    msg = str((item.get("error") or {}).get("message") or "")
                    if "temporarily exceeded" in msg.lower() and attempt < 2:
                        transient_err = msg
                        break
                    raise sqlite3.DatabaseError(f"Turso SQL 错误: {msg}")
                resp_obj = item.get("response") or {}
                if resp_obj.get("type") == "execute":
                    out.append(resp_obj.get("result") or {})
            if transient_err:
                time.sleep(0.5 * (attempt + 1))
                continue
            return out
        raise sqlite3.DatabaseError("Turso 服务短暂扩容限流，请稍后重试")

    def flush_writes(self) -> None:
        if not self._pending_writes:
            return
        writes = self._pending_writes
        self._pending_writes = []
        batch = [{"type": "execute", "stmt": {"sql": "BEGIN"}}] + writes + [{"type": "execute", "stmt": {"sql": "COMMIT"}}]
        self._send_pipeline(batch)

    def cursor(self) -> TursoHttpCursor:
        return TursoHttpCursor(self)

    def commit(self) -> None:
        self.flush_writes()

    def rollback(self) -> None:
        self._pending_writes = []

    def close(self) -> None:
        if self._pending_writes:
            try:
                self.flush_writes()
            except Exception:
                self._pending_writes = []

    def create_function(self, *args: Any, **kwargs: Any) -> None:
        pass


TURSO_DATABASE_URL = _get_clean_env("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = _extract_clean_jwt(_get_clean_env("TURSO_AUTH_TOKEN"))

if TURSO_DATABASE_URL:
    _clean_host = _normalize_turso_host(TURSO_DATABASE_URL)
    _scheme = "http" if _clean_host.startswith(("127.0.0.1", "localhost")) else "https"
    _pipeline_url = f"{_scheme}://{_clean_host}/v2/pipeline"
    _http_session = requests.Session()
    DB_MODE = "turso"
    engine = create_engine(
        "sqlite://",
        creator=lambda: TursoHttpConnection(_pipeline_url, TURSO_AUTH_TOKEN, _http_session),
        poolclass=NullPool,
    )
else:
    DB_MODE = "sqlite"
    DB_PATH = Path("/tmp/app.db") if IS_VERCEL else (BACKEND_DIR / "app.db")
    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_conn: Any, _) -> None:
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("SELECT name FROM sqlite_master LIMIT 1;")
        except sqlite3.OperationalError:
            try:
                cur.execute("PRAGMA journal_mode=DELETE;")
            except Exception:
                pass
        try:
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA busy_timeout=5000;")
        except Exception:
            pass
        cur.close()


def bulk_insert_models(session: Session, model_cls: Any, records: List[Dict[str, Any]]) -> None:
    if not records:
        return
    for r in records:
        for k, v in r.items():
            if isinstance(v, datetime):
                r[k] = v.strftime("%Y-%m-%d %H:%M:%S")
    session.execute(sa_insert(model_cls), records)


_db_initialized = False


def ensure_db_ready() -> None:
    """幂等冷启动数据库自检：先查 sqlite_master，仅当缺表时增量建表（兼容 SQLAlchemy Row 与并发冷启动）。"""
    global _db_initialized
    if _db_initialized:
        return
    table_names = list(SQLModel.metadata.tables.keys())
    try:
        with Session(engine) as session:
            if table_names:
                placeholders = ", ".join(f"'{name}'" for name in table_names)
                rows = session.exec(
                    text(f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})")
                ).all()
                existing_names = {
                    str(r[0] if hasattr(r, "__getitem__") and not isinstance(r, str) else r)
                    for r in rows
                }
                missing_tables = [
                    SQLModel.metadata.tables[name]
                    for name in table_names
                    if name not in existing_names
                ]
                if missing_tables:
                    SQLModel.metadata.create_all(engine, tables=missing_tables, checkfirst=True)
            else:
                SQLModel.metadata.create_all(engine, checkfirst=True)
        _db_initialized = True
    except Exception as exc:
        if "already exists" in str(exc).lower():
            _db_initialized = True
            return
        try:
            SQLModel.metadata.create_all(engine, checkfirst=True)
        except Exception as inner_exc:
            if "already exists" not in str(inner_exc).lower():
                raise
        _db_initialized = True


def get_session() -> Generator[Session, None, None]:
    ensure_db_ready()
    with Session(engine) as session:
        yield session

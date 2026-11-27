import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from database import _get_clean_env

GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
VALID_COL_TYPES = {"text", "number", "currency", "percent", "date"}

TABLE_EXTRACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Concise quotation or document header title visible in the image (e.g. '新军强 靓机 11.24').",
        },
        "document_notes": {
            "type": "string",
            "description": "Top/edge rules, validity notices, or vendor terms outside the table rows (empty string if none).",
        },
        "sheets": {
            "type": "array",
            "description": "List of ALL extracted tables/blocks in order from top to bottom.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Section or worksheet name (concise, under 28 characters).",
                    },
                    "headers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column header names for the table.",
                    },
                    "col_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["text", "number", "currency", "percent", "date"],
                        },
                        "description": "Semantic type of each column matching headers length.",
                    },
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "description": "ALL data rows from top to bottom. Every row must match headers length.",
                    },
                    "warnings": {
                        "type": "array",
                        "description": "Only genuinely blurry/uncertain cells.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "row": {"type": "integer"},
                                "col": {"type": "integer"},
                                "reason": {"type": "string"},
                            },
                            "required": ["row", "col", "reason"],
                        },
                    },
                },
                "required": ["name", "headers", "col_types", "rows"],
            },
        },
    },
    "required": ["title", "sheets"],
}


def _get_gemini_api_key() -> str:
    return _get_clean_env("GEMINI_API_KEY") or _get_clean_env("GOOGLE_API_KEY")


def has_gemini_key() -> bool:
    return bool(_get_gemini_api_key())


def get_active_model() -> str:
    return _get_clean_env("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def normalize_mime_type(filename: str, content_type: Optional[str]) -> str:
    ext = Path(filename or "").suffix.lower()
    ext_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".jfif": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
    }
    if content_type and content_type.startswith("image/"):
        if content_type == "image/jpg":
            return "image/jpeg"
        return content_type
    return ext_map.get(ext, "image/png")


def _looks_numeric(val: str) -> bool:
    cleaned = val.strip().replace(",", "").replace("¥", "").replace("$", "").replace("€", "").replace("%", "")
    if not cleaned or cleaned in ("-", "—", "N/A"):
        return True
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def _infer_col_type(values: List[str]) -> str:
    non_empty = [v.strip() for v in values if v and v.strip()]
    if not non_empty:
        return "text"
    # 带前导零编号（如 001042）强制保持文本，防止 Excel 丢零
    if any(re.match(r"^0\d+$", v.replace(",", "")) for v in non_empty):
        return "text"

    total = len(non_empty)
    pct_hits = sum(1 for v in non_empty if v.endswith("%") and _looks_numeric(v))
    if pct_hits == total or (total >= 2 and pct_hits / total >= 0.6):
        return "percent"

    cur_symbol_hits = sum(1 for v in non_empty if v.startswith(("¥", "$", "€", "£")) and _looks_numeric(v))
    if cur_symbol_hits >= 1 and (cur_symbol_hits / total >= 0.5):
        return "currency"

    num_hits = sum(1 for v in non_empty if _looks_numeric(v))
    if num_hits == total or (total >= 3 and num_hits / total >= 0.65):
        return "number"
    return "text"


def normalize_table_grid(payload: Dict[str, Any], fallback_name: str = "Sheet1") -> Dict[str, Any]:
    """
    R1 工程防呆：
    1. 统一对齐每行列数（缺格补 ""，多格自动扩展表头），严禁静默丢行；
    2. 校验 col_types 与合并单元格坐标边界；
    3. 自动补充疑似识别异常或类型不匹配单元格的 warnings 高亮提示。
    """
    raw_sheets = payload.get("sheets") or []
    if not raw_sheets:
        raw_sheets = [
            {
                "name": fallback_name[:28] or "Sheet1",
                "headers": ["内容"],
                "col_types": ["text"],
                "rows": [[payload.get("plain_text") or "未检测到结构化表格"]],
                "merges": [],
                "warnings": [{"row": 0, "col": 0, "reason": "未检测到明确表格边界，已回退为单列文本"}],
            }
        ]

    normalized_sheets: List[Dict[str, Any]] = []
    for idx, sheet in enumerate(raw_sheets):
        headers = [_sanitize_cell_text(h, is_header=True) for h in (sheet.get("headers") or [])]
        raw_rows = sheet.get("rows") or []
        str_rows: List[List[str]] = []
        max_cols = len(headers)
        for row in raw_rows:
            if isinstance(row, list):
                cells = [str(c if c is not None else "") for c in row]
            else:
                cells = [str(row if row is not None else "")]
            max_cols = max(max_cols, len(cells))
            str_rows.append(cells)

        if max_cols == 0:
            max_cols = 1
        while len(headers) < max_cols:
            headers.append(f"列{len(headers) + 1}")
        headers = [h or f"列{i + 1}" for i, h in enumerate(headers)]

        # 先做一次基础换行/空白清洗以便准确做「表头-数据语义对齐纠偏」
        pre_rows: List[List[str]] = []
        for row in str_rows:
            padded = list(row) + [""] * max(0, max_cols - len(row))
            pre_rows.append([_sanitize_cell_text(c) for c in padded[:max_cols]])

        headers, str_rows, max_cols = _repair_shifted_headers_and_trim_dummies(headers, str_rows, pre_rows, max_cols)

        roles = _detect_column_roles(headers)
        model_col = roles["model_col"]
        cap_col = roles["cap_col"]
        note_cols_set = set(roles["note_cols"])

        # 按列语义清洗每个单元格（彻底消除 15 PRO\nMAX 字面量换行与全空行）
        cleaned_str_rows: List[List[str]] = []
        row_remap: Dict[int, int] = {}
        for r_orig_i, row in enumerate(str_rows):
            while len(row) < max_cols:
                row.append("")
            clean_row = [
                _sanitize_cell_text(
                    cell,
                    is_note=(c_i in note_cols_set),
                    is_model=(c_i == model_col),
                    is_cap=(c_i == cap_col),
                )
                for c_i, cell in enumerate(row[:max_cols])
            ]
            # 过滤全空行（如图 2 中第 30 行这类空白绿行）
            if not any(clean_row):
                continue
            row_remap[r_orig_i] = len(cleaned_str_rows)
            cleaned_str_rows.append(clean_row)
        str_rows = cleaned_str_rows

        raw_types = [str(t).lower() for t in (sheet.get("col_types") or [])]
        col_types: List[str] = []
        for col_i in range(max_cols):
            t = raw_types[col_i] if col_i < len(raw_types) else ""
            if col_i == cap_col:
                t = "text"
            elif t not in VALID_COL_TYPES:
                col_vals = [r[col_i] for r in str_rows]
                t = _infer_col_type(col_vals)
            col_types.append(t)

        # 校验合并范围，确保不越界
        valid_merges: List[Dict[str, int]] = []
        row_count = len(str_rows)
        for m in (sheet.get("merges") or []):
            try:
                sr = row_remap[int(m["start_row"])]
                sc = int(m["start_col"])
                er = row_remap[int(m["end_row"])]
                ec = int(m["end_col"])
            except Exception:
                continue
            if 0 <= sr <= er < row_count and 0 <= sc <= ec < max_cols and (sr != er or sc != ec):
                valid_merges.append({"start_row": sr, "start_col": sc, "end_row": er, "end_col": ec})

        # 合并模型提示的 warnings 与本地规则巡检
        warnings_map: Dict[tuple, str] = {}
        for w in (sheet.get("warnings") or []):
            try:
                orig_wr = int(w["row"])
                wc = int(w["col"])
                if orig_wr in row_remap and 0 <= wc < max_cols:
                    warnings_map[(row_remap[orig_wr], wc)] = str(w.get("reason") or "模型提示需人工核对")
            except Exception:
                continue

        for c_idx, c_type in enumerate(col_types):
            if c_type in ("number", "currency", "percent"):
                for r_idx, row in enumerate(str_rows):
                    cell = row[c_idx]
                    if cell and not _looks_numeric(cell) and (r_idx, c_idx) not in warnings_map:
                        warnings_map[(r_idx, c_idx)] = f"该列预期为{c_type}，当前值「{cell}」可能含 OCR 杂字符"

        warnings_list = [
            {"row": r, "col": c, "reason": reason}
            for (r, c), reason in sorted(warnings_map.items())
        ]

        tab_name = str(sheet.get("name") or f"Sheet{idx + 1}").strip() or f"Sheet{idx + 1}"
        normalized_sheets.append(
            {
                "name": tab_name[:28],
                "headers": headers,
                "col_types": col_types,
                "rows": str_rows,
                "merges": valid_merges,
                "warnings": warnings_list,
            }
        )

    plain_text = str(payload.get("plain_text") or "").strip()
    if not plain_text and normalized_sheets:
        first = normalized_sheets[0]
        lines = [" | ".join(first["headers"]), " | ".join(["---"] * len(first["headers"]))]
        for r in first["rows"]:
            lines.append(" | ".join(r))
        plain_text = "\n".join(lines)

    return {
        "title": str(payload.get("title") or fallback_name).strip(),
        "document_notes": str(payload.get("document_notes") or "").strip(),
        "sheets": normalized_sheets,
        "plain_text": plain_text,
    }


def _pick_quote_title(raw_title: str, fallback_stem: str) -> str:
    t = (raw_title or "").strip()
    generic = {"table", "sheet", "sheet1", "extracted", "extracted table", "quotation", "未命名"}
    if not t or t.lower() in generic:
        return (fallback_stem or "报价单")[:28]
    return t[:28]


def _norm_header(h: str) -> str:
    return re.sub(r"[\s\-_/（）()]+", "", str(h or "").strip()).lower()


_CAP_VALUE_RE = re.compile(
    r"^(?:[\u4e00-\u9fa5a-z0-9+/\-]{1,14}\s+)?"
    r"(?:(?:4|6|8|12|16|18|20|24|32)(?:g|gb)?\+)?(?:(?:32|64|128|256|512|1024)(?:g|gb)?|(?:1|2)t(?:b)?)"
    r"(?:\s*[\u4e00-\u9fa5a-z0-9/\-]{1,12})?$",
    re.IGNORECASE,
)


def _is_capacity_value(val: str) -> bool:
    s = re.sub(r"\s+", " ", str(val or "").strip())
    if not s or len(s) > 28:
        return False
    # 拒绝纯 3~5 位非容量价格数字（如 1450 / 2650）
    if re.match(r"^\d{3,5}$", s) and s not in {"32", "64", "128", "256", "512", "1024"}:
        return False
    return bool(_CAP_VALUE_RE.match(s))


def _repair_shifted_headers_and_trim_dummies(
    headers: List[str],
    raw_rows: List[List[str]],
    pre_rows: List[List[str]],
    max_cols: int,
) -> Tuple[List[str], List[List[str]], int]:
    """
    核心自愈：
    1. 解决平板表头漏掉「小型号」或「版本」导致的整体左移 + 末尾多出 `列10` 问题：
       当检测到表头中 `容量` 在位置 `h_cap`，而实际数据中容量列在更右侧 `d_cap`（且末尾存在足够数量的 `列N` 占位表头），
       自动删除末尾占位表头并在 `h_cap` 之前插入语义列名（如 `小型号` / `版本`），使全部价格列与表头 100% 对齐！
    2. 剔除末尾完全空白且表头为自动占位名 `列N` 的幽灵空列。
    """
    if not pre_rows or max_cols <= 2:
        return headers, raw_rows, max_cols

    dummy_re = re.compile(r"^列\d+$")
    trailing_dummies = 0
    for h in reversed(headers):
        if dummy_re.match(h.strip()):
            trailing_dummies += 1
        else:
            break

    # 1. 检测「容量表头」与「真实容量数据列」是否存在右移错位
    if trailing_dummies > 0:
        hdr_cap_col: Optional[int] = None
        for i, h in enumerate(headers):
            hn = _norm_header(h)
            if any(k in hn for k in ("容量", "内存", "配置", "规格", "storage")):
                hdr_cap_col = i
                break

        sample_rows = [r for r in pre_rows if any(r)][:30]
        actual_cap_col: Optional[int] = None
        best_cap_ratio = 0.0
        for ci in range(1, min(6, max_cols)):
            non_empty = [r[ci] for r in sample_rows if ci < len(r) and r[ci]]
            if not non_empty:
                continue
            cap_hits = sum(1 for v in non_empty if _is_capacity_value(v))
            ratio = cap_hits / len(non_empty)
            if ratio >= 0.55 and ratio > best_cap_ratio:
                best_cap_ratio = ratio
                actual_cap_col = ci

        if hdr_cap_col is not None and actual_cap_col is not None and actual_cap_col > hdr_cap_col:
            delta = actual_cap_col - hdr_cap_col
            if delta <= trailing_dummies:
                # 根据漏掉列的数据内容智能命名（如 AST-W00 WIFI -> 小型号，行货/WIFI版 -> 版本）
                inferred_names: List[str] = []
                for offset in range(delta):
                    col_idx = hdr_cap_col + offset
                    col_vals = [r[col_idx] for r in sample_rows if col_idx < len(r) and r[col_idx]]
                    has_code_like = any(re.search(r"[A-Za-z]{2,}[\-\s]?\w*\d+", v) for v in col_vals)
                    has_ver_like = any(k in "".join(col_vals).upper() for k in ("WIFI", "插卡", "行货", "纳米", "柔光"))
                    if has_code_like and not any(n == "小型号" for n in inferred_names):
                        inferred_names.append("小型号")
                    elif has_ver_like:
                        inferred_names.append("版本")
                    else:
                        inferred_names.append("小型号" if offset == 0 else "规格")
                headers = headers[:hdr_cap_col] + inferred_names + headers[hdr_cap_col : max_cols - delta]

    # 2. 剔除末尾表头为 `列N` 且全部数据行该列皆空的幽灵空列
    while max_cols > 1 and dummy_re.match(headers[-1].strip()):
        last_c = max_cols - 1
        all_empty = all((last_c >= len(r) or not str(r[last_c]).strip()) for r in pre_rows)
        if not all_empty:
            break
        headers.pop()
        for r in raw_rows:
            if len(r) > last_c:
                del r[last_c]
        for r in pre_rows:
            if len(r) > last_c:
                del r[last_c]
        max_cols -= 1

    return headers, raw_rows, max_cols

_MODEL_BRAND_FIXES = [
    (re.compile(r"\biq00\b", re.IGNORECASE), "iQOO"),
    (re.compile(r"\b1qoo\b", re.IGNORECASE), "iQOO"),
    (re.compile(r"\biqoo\b", re.IGNORECASE), "iQOO"),
    (re.compile(r"\br0g\b", re.IGNORECASE), "ROG"),
    (re.compile(r"\b0pp0\b", re.IGNORECASE), "OPPO"),
]


def _sanitize_cell_text(
    raw_val: Any,
    is_note: bool = False,
    is_model: bool = False,
    is_cap: bool = False,
    is_header: bool = False,
) -> str:
    """
    统一清洗单元格文本：
    - 彻底消除模型双转义产生的字面量 `\\n`（如 `15 PRO\\nMAX` -> `15 PRO MAX`）；
    - 非备注列（型号/网络型号/容量/价格）折行规范为单空格，备注列保留真实换行；
    - 常见品牌 OCR 形近纠偏（如 `IQ00` -> `iQOO`）与容量水印噪点清理（如 `256 C` -> `256`）。
    """
    s = str(raw_val if raw_val is not None else "")
    if not s:
        return ""
    # 还原字面量 \r\n / \n / \t
    s = s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n").replace("\\t", " ")
    s = s.replace("\r\n", "\n").replace("\r", "\n")

    if is_note:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in s.split("\n")]
        return "\n".join(line for line in lines if line)

    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""

    if is_header:
        return s

    if is_model:
        for pat, repl in _MODEL_BRAND_FIXES:
            s = pat.sub(repl, s)
        return s

    if is_cap:
        # 剔除斜向水印混入容量的单字噪点（如 '256 C' -> '256'，不影响 '256G'/'1TB'/'12+256'）
        m_noise = re.match(r"^((?:\d+\+)?(?:\d+(?:G|GB)?|[12]T(?:B)?))\s+[A-Za-z]$", s, re.IGNORECASE)
        if m_noise:
            s = m_noise.group(1)
        return s

    return s


def _is_note_header(h: str) -> bool:
    hn = _norm_header(h)
    return any(k in hn for k in ("备注", "蓝光", "彩点", "面容", "拆修", "扣费", "说明", "规则", "note", "remark"))


def _detect_column_roles(headers: List[str]) -> Dict[str, Any]:
    """
    动态识别报价单各列语义角色（全品牌手机 + 平板多前置列通用）：
    兼容 `型号 | 容量 | 价格...`、`型号 | 网络型号 | 容量 | 价格...`、
    `Mate pad Air系列 | 小型号 | 容量 | 价格...`、以及 iPad `型号 | 网络型号 | 地区 | 版本 | 容量 | 价格...`。
    """
    sec_col: Optional[int] = None
    model_col: Optional[int] = None
    code_col: Optional[int] = None
    cap_col: Optional[int] = None
    meta_cols: List[int] = []
    note_cols: List[int] = []

    normed = [_norm_header(h) for h in (headers or [])]

    # 1. 先精确匹配主型号列（避免被「网络型号 / 小型号」抢占）
    for i, hn in enumerate(normed):
        if hn == "分类区块":
            sec_col = i
        elif hn in {"型号", "机型", "model", "设备名称", "手机型号", "平板型号"} and model_col is None:
            model_col = i

    # 2. 匹配编码列（含平板「小型号」）、容量列、备注列与系列主型号列
    for i, hn in enumerate(normed):
        if i in (sec_col, model_col):
            continue
        if code_col is None and any(k in hn for k in ("网络型号", "小型号", "型号编码", "机器编码", "代号", "sku", "编码")):
            code_col = i
        elif cap_col is None and any(k in hn for k in ("容量", "内存", "配置", "规格", "storage", "gb")):
            cap_col = i
        elif _is_note_header(headers[i]):
            note_cols.append(i)
        elif model_col is None and any(k in hn for k in ("型号", "机型", "model", "设备", "系列", "pad", "平板")):
            model_col = i

    # 兜底 1：若第 0 列是「*系列」（如 三星 折叠屏系列 / 魅族系列）且第 1 列是「具体型号」，则第 1 列为编码列
    if code_col is None and model_col == 0 and len(normed) > 1 and "具体型号" in normed[1]:
        code_col = 1

    # 兜底 2：若第 0 列非容量/备注/分类，且仍未识别出 model_col，则默认第 0 列为主型号列（如「Mate pad Air系列」）
    if model_col is None and normed and 0 not in (sec_col, code_col, cap_col) and 0 not in note_cols:
        if any(k in " ".join(normed) for k in ("容量", "内存", "靓机", "小花", "充新")):
            model_col = 0

    # 3. 识别位于容量列之前的额外描述列（如 iPad 的「地区/行货」「网络/版本/WIFI版」），归入 meta_cols 不当价格列
    for i, hn in enumerate(normed):
        if i in (sec_col, model_col, code_col, cap_col) or i in note_cols:
            continue
        if (cap_col is not None and i < cap_col) or any(k in hn for k in ("地区", "版本", "网络", "制式", "类型", "颜色", "屏幕", "成色说明")):
            meta_cols.append(i)

    excluded = {c for c in (sec_col, model_col, code_col, cap_col) if c is not None} | set(note_cols) | set(meta_cols)
    price_cols = [i for i in range(len(normed)) if i not in excluded]
    return {
        "sec_col": sec_col,
        "model_col": model_col,
        "code_col": code_col,
        "cap_col": cap_col,
        "meta_cols": meta_cols,
        "note_cols": note_cols,
        "price_cols": price_cols,
    }


def _norm_cap_str(val: str) -> str:
    s = re.sub(r"\s+", "", str(val or "").strip()).lower()
    if s.endswith("gb"):
        s = s[:-2] + "g"
    elif s.endswith("tb"):
        s = s[:-2] + "t"
    return s


def _cap_to_gb(val: str) -> Optional[int]:
    """
    将苹果单容量（64/128/256/512/1T/2T）、安卓 RAM+ROM（12+256/16+1TB）
    及平板带版本前缀容量（wifi版 8+256 / 柔光版 16+512）转为严格可比排序键。
    """
    s = _norm_cap_str(val)
    if not s:
        return None
    # 剥离平板常见前缀/后缀（wifi版 / 柔光版 / 纳米版 / 典藏版等）提取核心容量
    m_android = re.search(r"(\d+)g?\+(\d+(?:\.\d+)?)(tb|t|gb|g)?", s)
    if m_android:
        ram_num = int(m_android.group(1))
        rom_raw = float(m_android.group(2))
        unit = (m_android.group(3) or "").lower()
        rom_gb = int(round(rom_raw * 1024)) if unit.startswith("t") else int(round(rom_raw))
        if rom_gb in {16, 32, 64, 128, 256, 512, 1024, 2048}:
            return ram_num * 10000 + rom_gb
    m_t = re.search(r"(?:^|[^\d])(\d+(?:\.\d+)?)t(?:b)?(?:$|[^\da-z])", s)
    if m_t:
        return int(round(float(m_t.group(1)) * 1024))
    m_g = re.match(r"^(?:[^\d]*?)(\d+)g?$", s)
    if m_g:
        num = int(m_g.group(1))
        if num in {16, 32, 64, 128, 256, 512, 1024, 2048}:
            return num
    return None


def _extract_row_semantics(row: List[str], roles: Optional[Dict[str, Any]] = None) -> tuple[str, str, tuple]:
    """按列语义提取 (normalized_model, normalized_capacity, price_tuple)。"""
    r = row or []
    model_col = roles.get("model_col") if roles else None
    cap_col = roles.get("cap_col") if roles else None
    code_col = roles.get("code_col") if roles else None
    meta_cols = set(roles.get("meta_cols") or []) if roles else set()
    note_cols = set(roles.get("note_cols") or []) if roles else set()

    if model_col is None and r:
        model_col = 0
    if cap_col is None:
        # 动态兜底：在前 6 列中自动寻找形如 128/256G/512/1T/12+256/wifi版 8+256 的容量单元格
        for ci in range(1, min(6, len(r))):
            if ci == code_col or ci in meta_cols:
                continue
            if _is_capacity_value(str(r[ci] or "")):
                cap_col = ci
                break
        if cap_col is None and len(r) > 1:
            cap_col = 1

    m_raw = str(r[model_col] if model_col is not None and model_col < len(r) else "").strip()
    c_raw = str(r[cap_col] if cap_col is not None and cap_col < len(r) else "").strip()
    m_norm = re.sub(r"[\s\-_/]+", "", m_raw).lower()
    c_norm = _norm_cap_str(c_raw)

    skip_set = (
        {c for c in (model_col, cap_col, code_col, roles.get("sec_col") if roles else None) if c is not None}
        | note_cols
        | meta_cols
    )
    prices: List[str] = []
    for ci, val in enumerate(r):
        if ci in skip_set:
            continue
        v = re.sub(r"[¥$€£,\s]", "", str(val or ""))
        if re.match(r"^\d+(\.\d+)?$", v):
            prices.append(v)
    return m_norm, c_norm, tuple(prices)


def _models_compatible_norm(n1: str, n2: str) -> bool:
    if not n1 or not n2:
        return True
    if n1 == n2:
        return True
    return n1.startswith(n2) or n2.startswith(n1) or (len(n1) >= 2 and n1 in n2) or (len(n2) >= 2 and n2 in n1)


def _map_subsheet_columns(
    sh_headers: List[str],
    sh_types: List[str],
    unified_headers: List[str],
    unified_types: List[str],
    header_to_col: Dict[str, int],
    master_len: int,
) -> List[int]:
    """
    智能对齐同一报价单内各子表的列映射（0-indexed，直接对应原图真实列序，不增设分类列）：
    1. 列数与主表相同时，直接按位置 1:1 对齐；
    2. 列数少于主表时，先按列名精确/规范化锚点对齐；剩余未匹配的价格列只落入空闲的价格槽位，
       绝不会漂移挤进最右侧的「备注/扣费说明」槽位。
    """
    n = len(sh_headers)
    if n == master_len:
        return [i for i in range(n)]

    norm_master = {_norm_header(h): idx for idx, h in enumerate(unified_headers)}
    assigned: List[Optional[int]] = [None] * n
    used_targets: set = set()

    for i, h in enumerate(sh_headers):
        h_clean = str(h or f"列{i + 1}").strip() or f"列{i + 1}"
        if h_clean in header_to_col and header_to_col[h_clean] not in used_targets:
            t_col = header_to_col[h_clean]
            assigned[i] = t_col
            used_targets.add(t_col)
            continue
        nh = _norm_header(h_clean)
        if nh and nh in norm_master and norm_master[nh] not in used_targets:
            t_col = norm_master[nh]
            assigned[i] = t_col
            used_targets.add(t_col)

    unmatched_indices = [i for i, v in enumerate(assigned) if v is None]
    unmatched_notes = [i for i in unmatched_indices if _is_note_header(sh_headers[i])]
    unmatched_non_notes = [i for i in unmatched_indices if i not in unmatched_notes]

    free_note_slots = [c for c in range(master_len) if c not in used_targets and _is_note_header(unified_headers[c])]
    free_non_note_slots = [c for c in range(master_len) if c not in used_targets and not _is_note_header(unified_headers[c])]

    # 1. 非备注列（如价格档位列）优先右对齐填入空闲的非备注槽位
    if unmatched_non_notes and len(free_non_note_slots) >= len(unmatched_non_notes):
        offset = len(free_non_note_slots) - len(unmatched_non_notes)
        for k, local_idx in enumerate(unmatched_non_notes):
            slot = free_non_note_slots[offset + k]
            assigned[local_idx] = slot
            used_targets.add(slot)
    else:
        for local_idx in unmatched_non_notes:
            if free_non_note_slots:
                slot = free_non_note_slots.pop(0)
                assigned[local_idx] = slot
                used_targets.add(slot)
            else:
                h_clean = str(sh_headers[local_idx] or f"列{local_idx + 1}").strip()
                new_col = len(unified_headers)
                unified_headers.append(h_clean)
                t = sh_types[local_idx] if local_idx < len(sh_types) else "text"
                unified_types.append(t if t in VALID_COL_TYPES else "text")
                header_to_col[h_clean] = new_col
                assigned[local_idx] = new_col

    # 2. 备注/扣费说明列填入空闲备注槽位，不足时追加到最右侧
    for local_idx in unmatched_notes:
        if free_note_slots:
            slot = free_note_slots.pop(0)
            assigned[local_idx] = slot
            used_targets.add(slot)
        else:
            h_clean = str(sh_headers[local_idx] or f"备注{local_idx + 1}").strip()
            new_col = len(unified_headers)
            unified_headers.append(h_clean)
            unified_types.append("text")
            header_to_col[h_clean] = new_col
            assigned[local_idx] = new_col

    return [int(c if c is not None else 0) for c in assigned]


def _is_condition_legend_sheet(sheet: Dict[str, Any]) -> bool:
    """
    识别报价单顶部的「成色说明 / 等级说明」图例小表（如 成色 | 外屏 | 边框背板 | 内屏显示），
    防止其混入正式报价数据表或抢占主表头。
    """
    headers = [str(h or "").strip() for h in (sheet.get("headers") or [])]
    if not headers:
        return False
    joined_h = " ".join(headers)
    name_s = str(sheet.get("name") or "")
    roles = _detect_column_roles(headers)
    if roles["model_col"] is not None or roles["cap_col"] is not None:
        return False
    legend_hits = sum(
        1 for kw in ("成色说明", "等级说明", "成色等级", "外屏", "触摸外屏", "边框背板", "内屏显示")
        if kw in joined_h or kw in name_s
    )
    if legend_hits >= 2:
        return True
    rows = sheet.get("rows") or []
    if len(rows) <= 8:
        has_any_price = any(
            re.match(r"^\d{3,5}$", re.sub(r"[¥$€£,\s]", "", str(c or "")))
            for r in rows for c in r
        )
        if not has_any_price and legend_hits >= 1:
            return True
    return False


def _is_series_or_header_model(val: str) -> bool:
    """判断机型列值是否为表头占位词或反复出现的绿色「xxx系列」分段标题（绝非具体机型名）。"""
    s = str(val or "").strip()
    if not s:
        return True
    low = s.lower()
    if low in {"型号", "机型", "model", "小型号", "网络型号", "品牌型号", "具体型号", "型号名称", "商品名称"}:
        return True
    if s.endswith("系列") or bool(re.search(r"系列\s*/\s*型号$", s)):
        return True
    return False


def _extract_code_family_prefix(val: str) -> str:
    """从网络型号/小型号中提取机型族前缀（如 'MRO-W00 WIFI' -> 'mro', 'PCE-W30 WIFI版' -> 'pce'）。"""
    s = str(val or "").strip()
    if not s:
        return ""
    m = re.match(r"([A-Za-z0-9]{2,8})(?:[-/\s]|$)", s)
    return m.group(1).lower() if m else ""


def _code_family_key(val: str) -> str:
    """
    提取网络型号/具体型号的「机型族归属键」，用于严格拦截跨网码串机型：
    - 苹果 A 码（A2896 vs A2892）：每个 A 码唯一对应一款机型，保留全码；
    - 带连字符编码（JAD-AL00 / JAD-AL50, PNM-AN10 / VER-AN10, MRO-W00 / MRO-AL10）：取连字符前缀；
    - 三星 5 位码（A9080 / A8050 / A7070 / C5560 / S9480）：取前 3 位（a90 / a80 / a70 / c55 / s94）；
    - 安卓字母+数字码（V2454A / V2454DA, M582Q / M582V, RMX3663）：取字母+数字主干。
    """
    raw = str(val or "").strip()
    if not raw:
        return ""
    first_tok = re.split(r"[\s/(（]+", raw)[0].strip()
    if not first_tok or first_tok in {"-", "—", "不详", "无"}:
        return ""
    up = first_tok.upper()
    if re.match(r"^[A-Z]\d{4}$", up):
        # 苹果 A 码（A2896/A2892）与三星 5 位具体型号码（W9027/W7025/S9380/F9760/A9080/A8050/C5560）一码一机型，保留全码
        return up.lower()
    if "-" in up:
        prefix = up.split("-", 1)[0]
        if len(prefix) >= 2:
            return prefix.lower()
    m_core = re.match(r"^([A-Z]{1,4}\d{3,5})[A-Z]*$", up)
    if m_core:
        return m_core.group(1).lower()
    alnum = re.sub(r"[^A-Za-z0-9]", "", up).lower()
    return alnum[:6] if len(alnum) >= 3 else alnum


def _codes_same_family(c1: str, c2: str) -> bool:
    k1 = _code_family_key(c1)
    k2 = _code_family_key(c2)
    if not k1 or not k2:
        return True
    return k1 == k2


def _is_protected_blank_warning(reason: str) -> bool:
    s = str(reason or "")
    return "已留空" in s or "核验分歧" in s or "冲突" in s


def _is_summary_row(row: List[str]) -> bool:
    """判断是否为财务/通用表格的汇总行（合计/总计/小计/汇总），严禁被机型继承或当孤立残行丢弃。"""
    for cell in (row or [])[:3]:
        s = str(cell or "").strip().lower()
        if not s:
            continue
        if s in {"合计", "总计", "小计", "汇总", "共计", "total", "subtotal", "grand total"} or any(
            s.startswith(p) for p in ("合计", "总计", "小计", "汇总", "共计", "total:", "subtotal:")
        ):
            return True
    return False


def _is_quotation_sheet(
    headers: List[str],
    rows: List[List[str]],
    title: str = "",
    doc_type: str = "auto",
) -> bool:
    """
    自动区分「手机/平板报价单」与「通用多场景表格」（财务报表/发票/库存/考勤/日程等）：
    - doc_type='quotation' 强制报价单模式；
    - doc_type='general' 强制通用表格模式；
    - doc_type='auto' 基于表头语义、容量列实测占比与标题特征综合打分（>=2 判定为报价单）。
    """
    dt = (doc_type or "auto").strip().lower()
    if dt == "quotation":
        return True
    if dt == "general":
        return False

    h_list = [str(h or "").strip() for h in (headers or [])]
    h_join = " ".join(h_list)
    score = 0

    strong_quote_kws = ("充新", "靓机", "小花", "大花", "外爆", "内爆", "保修", "蓝光", "彩点", "扣费", "高保")
    strong_hits = sum(1 for kw in strong_quote_kws if kw in h_join)
    score += min(4, strong_hits * 2)

    roles = _detect_column_roles(h_list)
    cap_col = roles.get("cap_col")
    if cap_col is not None and rows:
        sample_caps = [str(r[cap_col]).strip() for r in rows[:25] if cap_col < len(r) and str(r[cap_col]).strip()]
        if sample_caps:
            cap_ratio = sum(1 for v in sample_caps if _is_capacity_value(v)) / len(sample_caps)
            if cap_ratio >= 0.5:
                score += 2

    if any(k in h_join for k in ("型号", "机型", "小型号", "网络型号")) or any(h.endswith("系列") for h in h_list):
        score += 1

    t_s = str(title or "")
    if any(k in t_s for k in ("报价", "回收", "靓机", "过保", "在保", "平板行货")):
        score += 1

    return score >= 2


def _should_keep_multi_sheets_separate(sheets: List[Dict[str, Any]], doc_type: str = "auto") -> bool:
    """
    当通用多场景图片包含多张结构迥异的表格（如发票上方买卖方信息表 + 下方商品明细表）时，
    自动建议拆分为独立 Sheet，避免硬拼成畸形单表。
    """
    if len(sheets) <= 1:
        return False
    dt = (doc_type or "auto").strip().lower()
    if dt == "quotation":
        return False
    if any(_is_quotation_sheet(s.get("headers") or [], s.get("rows") or [], s.get("name", ""), dt) for s in sheets):
        return False

    base_h = {_norm_header(h) for h in (sheets[0].get("headers") or []) if str(h or "").strip()}
    base_len = len(sheets[0].get("headers") or [])
    for sh in sheets[1:]:
        cur_h = {_norm_header(h) for h in (sh.get("headers") or []) if str(h or "").strip()}
        cur_len = len(sh.get("headers") or [])
        union_n = max(1, len(base_h | cur_h))
        overlap = len(base_h & cur_h) / union_n
        if overlap < 0.4 and (cur_len != base_len or overlap < 0.2):
            return True
    return False


def merge_sheets_into_single_quotation(
    sheets: List[Dict[str, Any]],
    quote_title: str,
    fallback_stem: str = "报价单",
    doc_type: str = "auto",
) -> Dict[str, Any]:
    """
    忠实原图合表（兼容报价单与通用表格双模式）：
    - 彻底去掉人工「分类/区块」列，A 列直接为原图第 1 列；
    - 报价单模式下自动过滤顶部「成色说明 / 等级说明」图例表并清理纯重复系列表头；
    - 通用表格模式下跳过报价单专属清洗，保护合计行与原始列结构，标记 style_preset='general'。
    """
    final_name = _pick_quote_title(quote_title, fallback_stem)
    if not sheets:
        return {
            "name": final_name,
            "headers": ["内容"],
            "col_types": ["text"],
            "rows": [[""]],
            "merges": [],
            "warnings": [],
            "section_header_rows": [],
            "style_preset": "general" if doc_type == "general" else "quotation",
        }

    any_quote = any(
        _is_quotation_sheet(sh.get("headers") or [], sh.get("rows") or [], quote_title or sh.get("name", ""), doc_type)
        for sh in sheets
    )

    # 仅在报价单场景下过滤顶部「成色说明」图例表
    if any_quote:
        non_legend = [sh for sh in sheets if not _is_condition_legend_sheet(sh)]
        candidate_sheets = non_legend if non_legend else sheets
    else:
        candidate_sheets = sheets

    # 先对每个区块独立清洗（防重复表头/残缺行/合并空型号），通用表则跳过报价单专属规则
    cleaned_sheets: List[Dict[str, Any]] = []
    for sh in candidate_sheets:
        sh_copy = json.loads(json.dumps(sh))
        sh_is_quote = _is_quotation_sheet(
            sh_copy.get("headers") or [],
            sh_copy.get("rows") or [],
            quote_title or sh_copy.get("name", ""),
            doc_type,
        )
        c_rows, c_warns = _clean_quotation_rows(
            sh_copy.get("headers") or [],
            sh_copy.get("rows") or [],
            sh_copy.get("warnings") or [],
            is_quotation=sh_is_quote,
        )
        if c_rows:
            sh_copy["rows"] = c_rows
            sh_copy["warnings"] = c_warns
            sh_copy["style_preset"] = "quotation" if sh_is_quote else "general"
            cleaned_sheets.append(sh_copy)

    if not cleaned_sheets:
        cleaned_sheets = [json.loads(json.dumps(sheets[0]))]

    if len(cleaned_sheets) == 1:
        single = cleaned_sheets[0]
        single["name"] = final_name
        single.setdefault("section_header_rows", [])
        return _global_post_clean_and_audit(single, doc_type=doc_type)

    # 选用列数最完整（同列数取第 1 个）的子表作为顶部主表头
    master_sheet = max(cleaned_sheets, key=lambda s: len(s.get("headers") or []))
    master_headers = [
        str(h or f"列{i + 1}").strip() or f"列{i + 1}"
        for i, h in enumerate(master_sheet.get("headers") or [])
    ]
    master_types = master_sheet.get("col_types") or []
    master_len = len(master_headers)

    unified_headers: List[str] = list(master_headers)
    unified_types: List[str] = [
        (master_types[i] if i < len(master_types) and master_types[i] in VALID_COL_TYPES else "text")
        for i in range(master_len)
    ]
    header_to_col: Dict[str, int] = {h: i for i, h in enumerate(master_headers)}

    mappings: List[List[int]] = []
    for sh in cleaned_sheets:
        sh_headers = [str(h or f"列{i + 1}").strip() or f"列{i + 1}" for i, h in enumerate(sh.get("headers") or [])]
        sh_types = sh.get("col_types") or []
        mappings.append(
            _map_subsheet_columns(sh_headers, sh_types, unified_headers, unified_types, header_to_col, master_len)
        )

    total_cols = len(unified_headers)
    merged_rows: List[List[str]] = []
    merged_warnings: List[Dict[str, Any]] = []
    section_header_rows: List[int] = []

    # 第 0 区块的表头已作为 Excel 第 1 行表头
    master_roles = _detect_column_roles(unified_headers)
    master_model_col = master_roles.get("model_col")
    skipped_series_header_variants = False
    seen_series_headers: set = set()
    if master_model_col is not None and master_model_col < len(unified_headers) and unified_headers[master_model_col].endswith("系列"):
        seen_series_headers.add(unified_headers[master_model_col])

    prev_header_row = [""] * total_cols
    first_headers = cleaned_sheets[0].get("headers") or []
    for i, h in enumerate(first_headers):
        if i < len(mappings[0]):
            prev_header_row[mappings[0][i]] = str(h or "").strip()

    for s_idx, sh in enumerate(cleaned_sheets):
        local_to_global = mappings[s_idx]
        cur_headers = sh.get("headers") or []
        cur_header_row = [""] * total_cols
        for i, h in enumerate(cur_headers):
            if i < len(local_to_global):
                cur_header_row[local_to_global[i]] = str(h or "").strip()
        if master_model_col is not None and master_model_col < len(cur_header_row) and cur_header_row[master_model_col].endswith("系列"):
            seen_series_headers.add(cur_header_row[master_model_col])

        # 若当前区块表头与上一区块存在实质差异（价格/容量/编码列有变化），保留区块小表头行；
        # 若仅末尾备注列为空，或仅第 1 列在「xxx系列 / 型号」间切换而右侧全部价格列完全一致，则视作纯重复系列表头予以合并清理！
        if s_idx > 0 and cur_header_row != prev_header_row and any(cur_header_row):
            non_model_diff = any(
                (ci != master_model_col)
                and (cur_header_row[ci] != prev_header_row[ci])
                and (cur_header_row[ci] != "" or not _is_note_header(prev_header_row[ci]))
                for ci in range(total_cols)
            )
            only_series_model_header_swap = (
                any_quote
                and not non_model_diff
                and master_model_col is not None
                and _is_series_or_header_model(cur_header_row[master_model_col])
                and _is_series_or_header_model(prev_header_row[master_model_col])
            )
            if non_model_diff:
                section_header_rows.append(len(merged_rows))
                merged_rows.append(cur_header_row)
                prev_header_row = cur_header_row
            elif only_series_model_header_swap:
                skipped_series_header_variants = True
                prev_header_row = cur_header_row

        base_row_offset = len(merged_rows)
        for row in (sh.get("rows") or []):
            new_row = [""] * total_cols
            for local_c, val in enumerate(row):
                if local_c < len(local_to_global):
                    new_row[local_to_global[local_c]] = str(val if val is not None else "")
            merged_rows.append(new_row)

        for w in (sh.get("warnings") or []):
            try:
                lr = int(w["row"])
                lc = int(w["col"])
                if 0 <= lc < len(local_to_global):
                    merged_warnings.append(
                        {
                            "row": base_row_offset + lr,
                            "col": local_to_global[lc],
                            "reason": str(w.get("reason") or "模型提示需人工核对"),
                        }
                    )
            except Exception:
                continue

    # 若同一张连续报价表涵盖多个不同「xxx系列」分段表头（如华为 Mate pad Air/Mate pad、苹果 Pro/Air/Mini），
    # 将顶部 A 列主表头归一为「型号」，避免首个系列名覆盖整表其他系列
    if (
        any_quote
        and (skipped_series_header_variants or len(seen_series_headers) >= 2)
        and master_model_col is not None
        and master_model_col < len(unified_headers)
        and unified_headers[master_model_col].endswith("系列")
    ):
        unified_headers[master_model_col] = "型号"

    result_sheet = {
        "name": final_name,
        "headers": unified_headers,
        "col_types": unified_types,
        "rows": merged_rows,
        "merges": [],
        "warnings": merged_warnings,
        "section_header_rows": section_header_rows,
        "style_preset": "quotation" if any_quote else "general",
    }
    return _global_post_clean_and_audit(result_sheet, doc_type=doc_type)


def _global_post_clean_and_audit(sheet: Dict[str, Any], doc_type: str = "auto") -> Dict[str, Any]:
    """
    合表后全局二次精洗与异常逻辑自检：
    - 先识别当前表是「报价单」还是「通用多场景表格」并写入 style_preset；
    - 通用表格仅保留原始结构与原有 warnings，严禁运行报价单专属的容量/成色档位自检与去重规则；
    - 报价单则执行跨块幽灵行剔除、网络型号分组一致性纠偏、xxx系列误填回填与价格逻辑自检。
    """
    headers: List[str] = sheet.get("headers") or []
    rows: List[List[str]] = sheet.get("rows") or []
    warnings: List[Dict[str, Any]] = list(sheet.get("warnings") or [])
    sec_headers_set = set(int(x) for x in (sheet.get("section_header_rows") or []))

    is_quote = _is_quotation_sheet(headers, rows, sheet.get("name", ""), doc_type)
    sheet["style_preset"] = "quotation" if is_quote else "general"

    if not headers or not rows or not is_quote:
        return sheet

    roles = _detect_column_roles(headers)
    model_col = roles["model_col"]
    cap_col = roles["cap_col"]
    code_col = roles["code_col"]
    price_cols: List[int] = roles["price_cols"]

    if model_col is None:
        return sheet

    # 预计算每行语义（型号/容量/容量GB/价格序列）
    sem_list = [_extract_row_semantics(r, roles) for r in rows]

    # 检测每行之后同机型是否出现容量明显回跳（例如 13 先出现 512/1T，后面又从 128 重新起排）
    has_later_smaller_cap: List[bool] = [False] * len(rows)
    for i in range(len(rows)):
        if i in sec_headers_set:
            continue
        m_i, c_i, _ = sem_list[i]
        gb_i = _cap_to_gb(c_i)
        if not m_i or gb_i is None:
            continue
        for j in range(i + 1, min(len(rows), i + 6)):
            if j in sec_headers_set:
                break
            m_j, c_j, _ = sem_list[j]
            if m_j != m_i:
                break
            gb_j = _cap_to_gb(c_j)
            if gb_j is not None and gb_j < gb_i:
                has_later_smaller_cap[i] = True
                break

    new_rows: List[List[str]] = []
    new_sec_headers: List[int] = []
    idx_map: Dict[int, int] = {}
    new_sems: List[tuple[str, str, tuple]] = []
    segment_start = 0

    for orig_i, r in enumerate(rows):
        if orig_i in sec_headers_set:
            idx_map[orig_i] = len(new_rows)
            new_sec_headers.append(len(new_rows))
            new_rows.append(list(r))
            new_sems.append(("", "", ()))
            segment_start = len(new_rows)
            continue

        m_cur, c_cur, p_cur = sem_list[orig_i]
        matched_prev: Optional[int] = None

        if len(p_cur) >= 2:
            lookback_start = max(segment_start, len(new_rows) - 6)
            cur_code_norm = (
                re.sub(r"\s+", "", str(r[code_col])).lower()
                if code_col is not None and code_col < len(r)
                else ""
            )
            for prev_i in range(len(new_rows) - 1, lookback_start - 1, -1):
                m_prev, c_prev, p_prev = new_sems[prev_i]
                same_cap = (cap_col is None) or (bool(c_cur) and c_cur == c_prev)
                if same_cap and p_prev == p_cur:
                    prev_code_norm = (
                        re.sub(r"\s+", "", str(new_rows[prev_i][code_col])).lower()
                        if code_col is not None and code_col < len(new_rows[prev_i])
                        else ""
                    )
                    # 若两行均有非空且不同的网络型号编码（如 小米12S Pro 2206122SC vs 小米12S 2206123SC）
                    # 且当前行并非容量回跳幽灵行，则必为两款独立机型，严禁误合并！
                    if cur_code_norm and prev_code_norm and cur_code_norm != prev_code_norm and not has_later_smaller_cap[orig_i]:
                        continue
                    if _models_compatible_norm(m_prev, m_cur) or has_later_smaller_cap[orig_i]:
                        matched_prev = prev_i
                        break

        if matched_prev is not None:
            # 保留前序更完整机型（如 13 PRO + A2639），只补全空白的备注/非编码列，绝不用幽灵行的网络型号覆盖
            pr = new_rows[matched_prev]
            m_prev = new_sems[matched_prev][0]
            prefer_cur_model = len(m_cur) > len(m_prev) and not has_later_smaller_cap[orig_i]
            if prefer_cur_model and model_col < len(pr) and model_col < len(r):
                pr[model_col] = r[model_col]
                if code_col is not None and code_col < len(pr) and code_col < len(r) and str(r[code_col]).strip():
                    pr[code_col] = r[code_col]
                new_sems[matched_prev] = _extract_row_semantics(pr, roles)
            for ci in range(min(len(pr), len(r))):
                if ci in (model_col, code_col, cap_col):
                    continue
                if not str(pr[ci]).strip() and str(r[ci]).strip():
                    pr[ci] = r[ci]
            idx_map[orig_i] = matched_prev
        else:
            idx_map[orig_i] = len(new_rows)
            new_rows.append(list(r))
            new_sems.append((m_cur, c_cur, p_cur))

    # 1.5 网络型号分组一致性自动纠偏：
    # 典型案例：过保 R63 把 A2639 第一行误延用上个机型名 '13 PRO MAX'，但同一 A2639 组后续 3 行均为 '13 PRO'
    # 且容量严格递增（128 < 256 < 512 < 1T）且该误延用机型在前序已出现相同容量 -> 100% 确定性纠准为 '13 PRO'！
    if code_col is not None:
        sec_set_mid = set(new_sec_headers)
        i_run = 0
        while i_run < len(new_rows):
            if i_run in sec_set_mid:
                i_run += 1
                continue
            code_val = re.sub(r"\s+", "", str(new_rows[i_run][code_col] if code_col < len(new_rows[i_run]) else "")).lower()
            if not code_val:
                i_run += 1
                continue
            j_run = i_run + 1
            while j_run < len(new_rows) and j_run not in sec_set_mid:
                c_next = re.sub(r"\s+", "", str(new_rows[j_run][code_col] if code_col < len(new_rows[j_run]) else "")).lower()
                if c_next != code_val:
                    break
                j_run += 1

            run_len = j_run - i_run
            if run_len >= 2:
                # 检查该网络型号连续段内是否容量严格递增（无重启，排除如 华为 MATE XT2 灵盾防窥屏 2 行 + 普通版重启的情况）
                gbs = [_cap_to_gb(new_sems[k][1]) for k in range(i_run, j_run)]
                strictly_inc = all(g is not None for g in gbs) and all(gbs[t] < gbs[t + 1] for t in range(len(gbs) - 1))
                if strictly_inc:
                    models_in_run = [str(new_rows[k][model_col]).strip() for k in range(i_run, j_run)]
                    norm_models = [new_sems[k][0] for k in range(i_run, j_run)]
                    # 仅当边界第 1 行机型名与同组其余全部行不同，且与紧邻前一行机型名相同 -> 判定为上行多延 1 行
                    if len(set(norm_models[1:])) == 1 and norm_models[0] != norm_models[1]:
                        prev_m_norm = new_sems[i_run - 1][0] if (i_run > 0 and (i_run - 1) not in sec_set_mid) else ""
                        if prev_m_norm and norm_models[0] == prev_m_norm:
                            new_rows[i_run][model_col] = models_in_run[1]
                            new_sems[i_run] = _extract_row_semantics(new_rows[i_run], roles)
                    # 对称检查：仅当边界最后 1 行机型名与同组前面全部行不同，且与紧邻下一行机型名相同 -> 判定为下行提前拉 1 行
                    elif len(set(norm_models[:-1])) == 1 and norm_models[-1] != norm_models[0]:
                        next_m_norm = new_sems[j_run][0] if (j_run < len(new_rows) and j_run not in sec_set_mid) else ""
                        if next_m_norm and norm_models[-1] == next_m_norm:
                            new_rows[j_run - 1][model_col] = models_in_run[0]
                            new_sems[j_run - 1] = _extract_row_semantics(new_rows[j_run - 1], roles)
            i_run = j_run

    # 1.6 系列/表头占位机型自动回填纠偏：
    # 典型案例：华为平板绿色重复表头「Mate pad系列」紧贴下方超高 8 行合并单元格「Mate pad Pro 2024年 12.2寸」，
    # 注意：只修复非空的「xxx系列 / 型号」表头占位词，绝不覆盖双模型核验或防串行规则刻意留空的 "" 单元格！
    sec_set_series = set(new_sec_headers)
    i_ph = 0
    while i_ph < len(new_rows):
        if i_ph in sec_set_series or model_col >= len(new_rows[i_ph]):
            i_ph += 1
            continue
        cur_m_raw = str(new_rows[i_ph][model_col] or "").strip()
        if not cur_m_raw or not _is_series_or_header_model(cur_m_raw):
            i_ph += 1
            continue
        j_ph = i_ph + 1
        while (
            j_ph < len(new_rows)
            and j_ph not in sec_set_series
            and model_col < len(new_rows[j_ph])
            and bool(str(new_rows[j_ph][model_col] or "").strip())
            and _is_series_or_header_model(new_rows[j_ph][model_col])
        ):
            j_ph += 1

        # 向下寻找最近的真实机型行（限同一连续区块内 12 行）
        down_idx: Optional[int] = None
        for d in range(j_ph, min(len(new_rows), j_ph + 12)):
            if d in sec_set_series:
                break
            if model_col < len(new_rows[d]) and not _is_series_or_header_model(new_rows[d][model_col]):
                down_idx = d
                break

        # 向上寻找最近的真实机型行（限同一连续区块内 12 行）
        up_idx: Optional[int] = None
        for u in range(i_ph - 1, max(-1, i_ph - 13), -1):
            if u in sec_set_series:
                break
            if model_col < len(new_rows[u]) and not _is_series_or_header_model(new_rows[u][model_col]):
                up_idx = u
                break

        down_model = str(new_rows[down_idx][model_col]).strip() if down_idx is not None else ""
        up_model = str(new_rows[up_idx][model_col]).strip() if up_idx is not None else ""
        down_code_raw = (
            str(new_rows[down_idx][code_col]).strip()
            if (down_idx is not None and code_col is not None and code_col < len(new_rows[down_idx]))
            else ""
        )
        up_code_raw = (
            str(new_rows[up_idx][code_col]).strip()
            if (up_idx is not None and code_col is not None and code_col < len(new_rows[up_idx]))
            else ""
        )
        down_prefix = _extract_code_family_prefix(down_code_raw) if down_code_raw else ""
        up_prefix = _extract_code_family_prefix(up_code_raw) if up_code_raw else ""

        # 收集占位段内全部小型号前缀，辅助判断整段归属
        run_prefixes = {
            _extract_code_family_prefix(new_rows[k][code_col])
            for k in range(i_ph, j_ph)
            if code_col is not None and code_col < len(new_rows[k])
        } - {""}

        step16_blank_rows: List[Tuple[int, str, str]] = []
        for k in range(i_ph, j_ph):
            cur_code_raw = (
                str(new_rows[k][code_col]).strip()
                if (code_col is not None and code_col < len(new_rows[k]))
                else ""
            )
            cur_prefix = _extract_code_family_prefix(cur_code_raw) if cur_code_raw else ""
            cur_fam = _code_family_key(cur_code_raw) if cur_code_raw else ""
            down_fam = _code_family_key(down_code_raw) if down_code_raw else ""
            up_fam = _code_family_key(up_code_raw) if up_code_raw else ""

            chosen_model = ""
            if down_model and ((cur_prefix and down_prefix and cur_prefix == down_prefix) or (cur_fam and down_fam and cur_fam == down_fam)):
                chosen_model = down_model
            elif up_model and ((cur_prefix and up_prefix and cur_prefix == up_prefix) or (cur_fam and up_fam and cur_fam == up_fam)):
                chosen_model = up_model
            elif cur_fam and (down_fam or up_fam):
                # 当前「xxx系列」占位行带有明确网码，但与上下真实机型网码均不同族（如 W9027 vs W7025）-> 严禁瞎套邻行机型，留空标黄！
                orig_ph = str(new_rows[k][model_col]).strip()
                new_rows[k][model_col] = ""
                new_sems[k] = _extract_row_semantics(new_rows[k], roles)
                step16_blank_rows.append((k, orig_ph, cur_code_raw))
            elif down_model and (not up_prefix or down_prefix in run_prefixes or not run_prefixes):
                chosen_model = down_model
            elif down_model:
                chosen_model = down_model
            elif up_model:
                chosen_model = up_model

            if chosen_model:
                new_rows[k][model_col] = chosen_model
                new_sems[k] = _extract_row_semantics(new_rows[k], roles)

        if step16_blank_rows:
            for blk_r, orig_ph, blk_code in step16_blank_rows:
                warnings.append(
                    {
                        "row": blk_r,
                        "col": model_col,
                        "reason": f"防错填拦截：原识别为系列占位「{orig_ph}」且网码「{blk_code}」与邻行不同，已留空请对照原图补录",
                        "_already_mapped": True,
                    }
                )

        i_ph = j_ph

    # 映射原有 warnings
    new_warnings: List[Dict[str, Any]] = []
    warn_keys: set = set()
    for w in warnings:
        try:
            col_c = int(w["col"])
            if w.get("_already_mapped"):
                nr = int(w["row"])
            else:
                orig_r = int(w["row"])
                if orig_r not in idx_map:
                    continue
                nr = idx_map[orig_r]
            if (nr, col_c) not in warn_keys:
                warn_keys.add((nr, col_c))
                new_warnings.append({"row": nr, "col": col_c, "reason": str(w.get("reason") or "")})
        except Exception:
            continue

    # 1.7 跨网码族同机型串行拦截（「宁愿留空也不瞎填」核心护栏）：
    # 典型实测案例：
    # - 三星 R130「A80 | A9080」与 R131「A80 | A8050」同机型不同网码族；
    # - 三星 R134~135「A70S | C5560」承接 R133「A70S | A7070」跨族串行；
    # - 苹果过保 R49~52「14 PRO MAX | A2892」紧接 R45~48「14 PRO MAX | A2896」跨 A 码串行；
    # - 荣耀1 R100~103「Magic V2 5G | PNM-AN10」与 R116「Magic V2 5G | VER-AN10」跨族冲突。
    if code_col is not None:
        sec_set_cc = set(new_sec_headers)
        # 统计每个 code_family 关联了哪些机型名（用于判断如 VER-AN10 已被 Magic V2 至臻版/RSR 佐证）
        fam_to_models: Dict[str, set] = {}
        for r_i, row_val in enumerate(new_rows):
            if r_i in sec_set_cc or code_col >= len(row_val) or model_col >= len(row_val):
                continue
            fk = _code_family_key(row_val[code_col])
            mn = new_sems[r_i][0]
            if fk and mn:
                fam_to_models.setdefault(fk, set()).add(mn)

        # 按连续段归组 (m_norm, code_family) -> 列表 [(start_row, end_row, code_fam, raw_code, caps_set)]
        model_runs: Dict[str, List[Dict[str, Any]]] = {}
        r_cursor = 0
        while r_cursor < len(new_rows):
            if r_cursor in sec_set_cc or model_col >= len(new_rows[r_cursor]) or code_col >= len(new_rows[r_cursor]):
                r_cursor += 1
                continue
            mn = new_sems[r_cursor][0]
            fk = _code_family_key(new_rows[r_cursor][code_col])
            if not mn or not fk:
                r_cursor += 1
                continue
            r_end = r_cursor + 1
            caps_set = {new_sems[r_cursor][1]} if new_sems[r_cursor][1] else set()
            while (
                r_end < len(new_rows)
                and r_end not in sec_set_cc
                and model_col < len(new_rows[r_end])
                and code_col < len(new_rows[r_end])
                and new_sems[r_end][0] == mn
                and _code_family_key(new_rows[r_end][code_col]) == fk
            ):
                if new_sems[r_end][1]:
                    caps_set.add(new_sems[r_end][1])
                r_end += 1
            model_runs.setdefault(mn, []).append(
                {
                    "start": r_cursor,
                    "end": r_end,
                    "fam": fk,
                    "raw_code": str(new_rows[r_cursor][code_col]).strip(),
                    "raw_model": str(new_rows[r_cursor][model_col]).strip(),
                    "caps": caps_set,
                }
            )
            r_cursor = r_end

        for mn, runs in model_runs.items():
            distinct_fams = {g["fam"] for g in runs}
            if len(distinct_fams) < 2:
                continue

            def _run_affinity(g: Dict[str, Any]) -> int:
                score = 0
                fam = g["fam"]
                # 1) 网码族前缀直接命中机型名（如 a8050 -> a80 in 'a80', a7070 -> a70 in 'a70s', w7025 -> w25 in 'w25flip5g'）
                if len(fam) >= 3 and fam in mn:
                    score += 3
                elif re.match(r"^[a-z]\d{4}$", fam):
                    if fam[:3] in mn or (fam[0] == "w" and f"w{fam[-2:]}" in mn):
                        score += 3
                # 2) 同网码族还出现过该机型的衍生版本（如 VER-AN10 同时出现 Magic V2 至臻版/RSR，剥离 4G/5G 后缀比对基名）
                mn_base = re.sub(r"[45]g$", "", mn)
                if any(
                    other != mn
                    and (
                        mn in other
                        or other in mn
                        or (len(mn_base) >= 4 and (mn_base in other or (len(re.sub(r"[45]g$", "", other)) >= 4 and re.sub(r"[45]g$", "", other) in mn_base)))
                    )
                    for other in fam_to_models.get(g["fam"], set())
                ):
                    score += 2
                return score

            affinities = [_run_affinity(g) for g in runs]
            max_aff = max(affinities)
            suspect_indices: List[int] = []

            if max_aff > 0:
                # 有明确归属佐证的保留，无佐证的冲突段一律留空标黄
                suspect_indices = [idx for idx, af in enumerate(affinities) if af < max_aff]
            else:
                # 无前缀字面佐证时（如 14 PRO MAX 跨 A2896 / A2892）：若后续段容量与前段重合，或紧贴前段无缝切换不同网码族 -> 判为串机型留空
                seen_caps_in_model: set = set(runs[0]["caps"])
                for idx in range(1, len(runs)):
                    g_cur = runs[idx]
                    g_prev = runs[idx - 1]
                    has_cap_overlap = bool(g_cur["caps"] & seen_caps_in_model)
                    is_adjacent_switch = g_cur["start"] == g_prev["end"]
                    if has_cap_overlap or is_adjacent_switch:
                        suspect_indices.append(idx)
                    seen_caps_in_model |= g_cur["caps"]

            for s_idx in suspect_indices:
                g_bad = runs[s_idx]
                other_codes = sorted({g["raw_code"] for j, g in enumerate(runs) if j != s_idx and g["raw_code"] != g_bad["raw_code"]})
                other_hint = " / ".join(other_codes[:2])
                for row_k in range(g_bad["start"], g_bad["end"]):
                    new_rows[row_k][model_col] = ""
                    new_sems[row_k] = _extract_row_semantics(new_rows[row_k], roles)
                    if (row_k, model_col) not in warn_keys:
                        warn_keys.add((row_k, model_col))
                        new_warnings.append(
                            {
                                "row": row_k,
                                "col": model_col,
                                "reason": f"防错填拦截：原识别「{g_bad['raw_model']}」与网码「{g_bad['raw_code']}」跨族冲突（前序网码 {other_hint}），已留空请对照原图确认",
                            }
                        )

    # 全局逻辑自检（按区块段检查重复容量、缺价、价格档位倒挂）
    sec_set_new = set(new_sec_headers)
    segments: List[List[int]] = []
    cur_seg: List[int] = []
    for i in range(len(new_rows)):
        if i in sec_set_new:
            if cur_seg:
                segments.append(cur_seg)
                cur_seg = []
        else:
            cur_seg.append(i)
    if cur_seg:
        segments.append(cur_seg)

    def _add_warn(r_idx: int, c_idx: int, reason: str) -> None:
        if 0 <= c_idx < len(headers) and (r_idx, c_idx) not in warn_keys:
            warn_keys.add((r_idx, c_idx))
            new_warnings.append({"row": r_idx, "col": c_idx, "reason": reason})

    for seg in segments:
        # 1. 检测该段是否具备明显的「从左到右价格递减」主导趋势
        dec_pairs = 0
        total_pairs = 0
        seg_has_prices = False
        row_nums: Dict[int, List[Tuple[int, float]]] = {}
        for r_idx in seg:
            r = new_rows[r_idx]
            nums: List[Tuple[int, float]] = []
            for pc in price_cols:
                if pc < len(r):
                    v = re.sub(r"[¥$€£,\s]", "", str(r[pc] or ""))
                    if re.match(r"^\d+(\.\d+)?$", v):
                        nums.append((pc, float(v)))
            row_nums[r_idx] = nums
            if nums:
                seg_has_prices = True
            for k in range(len(nums) - 1):
                total_pairs += 1
                if nums[k][1] >= nums[k + 1][1]:
                    dec_pairs += 1

        is_descending_section = total_pairs >= 6 and (dec_pairs / total_pairs) >= 0.85

        # 2. 逐行检查：同机型(同编码+同前置版本列)重复容量 / 容量列疑似错位成价格 / 全价位空缺 / 价格档位严重倒挂
        meta_cols_list: List[int] = roles.get("meta_cols") or []
        seen_model_cap: Dict[Tuple[str, ...], int] = {}
        for r_idx in seg:
            m_norm, c_norm, _ = new_sems[r_idx]
            nums = row_nums[r_idx]
            code_norm = (
                re.sub(r"\s+", "", str(new_rows[r_idx][code_col])).lower()
                if code_col is not None and code_col < len(new_rows[r_idx])
                else ""
            )
            meta_norm = tuple(
                re.sub(r"\s+", "", str(new_rows[r_idx][mc])).lower() if mc < len(new_rows[r_idx]) else ""
                for mc in meta_cols_list
            )
            if m_norm and c_norm:
                key = (m_norm, code_norm, c_norm) + meta_norm
                if key in seen_model_cap:
                    target_c = cap_col if cap_col is not None else model_col
                    raw_m = str(new_rows[r_idx][model_col]).strip()
                    raw_c = str(new_rows[r_idx][cap_col]).strip() if cap_col is not None else c_norm
                    _add_warn(
                        r_idx,
                        target_c,
                        f"自检提示：同机型「{raw_m}」出现重复容量「{raw_c}」，请对照原图确认是否多识别行",
                    )
                else:
                    seen_model_cap[key] = r_idx

            # 检查容量列是否误入非容量的大额价格数字（例如 2650 左移进了容量列）
            if cap_col is not None and cap_col < len(new_rows[r_idx]):
                raw_cap = str(new_rows[r_idx][cap_col]).strip()
                if re.match(r"^\d{3,5}$", raw_cap) and _cap_to_gb(raw_cap) is None:
                    _add_warn(
                        r_idx,
                        cap_col,
                        f"自检提示：容量列数值「{raw_cap}」疑似价格左移错位，请对照原图核对",
                    )

            if seg_has_prices and m_norm and (cap_col is None or c_norm) and not nums and price_cols:
                _add_warn(r_idx, price_cols[0], "自检提示：该行未识别到价格数值，请对照原图补录")

            if is_descending_section and len(nums) >= 2:
                for k in range(len(nums) - 1):
                    left_val = nums[k][1]
                    right_col, right_val = nums[k + 1]
                    if left_val > 0 and right_val > left_val * 1.15:
                        _add_warn(
                            r_idx,
                            right_col,
                            f"自检提示：该格价格 ({int(right_val)}) 高于左侧更高成色档位 ({int(left_val)})，疑似错位请核对",
                        )

    sheet["rows"] = new_rows
    sheet["section_header_rows"] = new_sec_headers
    sheet["warnings"] = new_warnings
    return sheet


def _clean_quotation_rows(
    headers: List[str],
    rows: List[List[str]],
    warnings: List[Dict[str, Any]],
    is_quotation: bool = True,
) -> tuple[List[List[str]], List[Dict[str, Any]]]:
    """
    智能清洗异常行（区分报价单与通用表格）：
    - 通用表格（is_quotation=False）：仅剔除与顶部表头完全一致的重复内嵌表头行，完整保留合计/小计行、空白首列分组行与全部原始数据；
    - 报价单（is_quotation=True）：剔除重复表头与切缝残缺行，自动向下继承多层嵌套合并列，并保护合计/小计行不被覆盖。
    """
    if not rows or not headers:
        return rows, warnings

    if not is_quotation:
        norm_hdr = [re.sub(r"\s+", "", str(h or "")).lower() for h in headers]
        kept_rows: List[List[str]] = []
        row_map: Dict[int, int] = {}
        for idx, r in enumerate(rows):
            norm_r = [re.sub(r"\s+", "", str(c or "")).lower() for c in r[: len(norm_hdr)]]
            if norm_r == norm_hdr and any(norm_hdr):
                continue
            row_map[idx] = len(kept_rows)
            kept_rows.append(list(r))
        mapped_warns: List[Dict[str, Any]] = []
        for w in (warnings or []):
            try:
                orig_r = int(w["row"])
                if orig_r in row_map:
                    mapped_warns.append(
                        {"row": row_map[orig_r], "col": int(w["col"]), "reason": str(w.get("reason") or "")}
                    )
            except Exception:
                continue
        return kept_rows, mapped_warns

    roles = _detect_column_roles(headers)
    model_col: Optional[int] = roles["model_col"]
    cap_col: Optional[int] = roles["cap_col"]
    sec_col: Optional[int] = roles["sec_col"]
    code_col: Optional[int] = roles["code_col"]
    meta_cols_list: List[int] = roles.get("meta_cols") or []
    meta_cols_set = set(meta_cols_list)
    note_cols_set = set(roles["note_cols"])

    if model_col is None:
        return rows, warnings

    header_tokens = {"型号", "机型", "model", "小型号", "网络型号", "容量", "内存", "备注", "扣费项", "蓝光", "蓝光/彩点", "面容/拆修"}

    def _is_embedded_header(r: List[str]) -> bool:
        m_val = (r[model_col] if model_col < len(r) else "").strip().lower()
        c_val = (r[cap_col] if cap_col is not None and cap_col < len(r) else "").strip().lower()
        if m_val in {"型号", "机型", "model"} or m_val.endswith("系列"):
            if c_val in {"容量", "内存"} or any("靓机" in str(cell) or "充新" in str(cell) for cell in r):
                return True
        if c_val in {"容量", "内存"}:
            hits = sum(1 for cell in r if cell.strip().lower() in header_tokens or "靓机" in cell or "小花" in cell or "充新" in cell)
            if hits >= 2:
                return True
        return False

    def _price_sig(r: List[str]) -> tuple:
        vals = []
        for idx, val in enumerate(r):
            if idx in (model_col, cap_col, sec_col, code_col) or idx in note_cols_set or idx in meta_cols_set:
                continue
            v = re.sub(r"[¥$€£,\s]", "", str(val or ""))
            if re.match(r"^\d+(\.\d+)?$", v):
                vals.append((idx, v))
        return tuple(vals)

    # 第一遍：过滤重复表头行
    step1_rows: List[List[str]] = []
    step1_map: Dict[int, int] = {}
    for idx, r in enumerate(rows):
        if _is_embedded_header(r):
            continue
        step1_map[idx] = len(step1_rows)
        step1_rows.append(list(r))

    def _norm_model_name(m: str) -> str:
        return re.sub(r"[\s\-_/]+", "", str(m or "").strip()).lower()

    def _models_compatible(m1: str, m2: str) -> bool:
        n1, n2 = _norm_model_name(m1), _norm_model_name(m2)
        if not n1 or not n2:
            return True
        if n1 == n2:
            return True
        return n1.startswith(n2) or n2.startswith(n1) or (len(n1) >= 2 and n1 in n2) or (len(n2) >= 2 and n2 in n1)

    def _merge_dup_pair(row_a: List[str], row_b: List[str]) -> List[str]:
        """合并相邻重复行：优先保留更长更完整的机型名，并补全任一方缺失的备注/扣费项。"""
        na = _norm_model_name(row_a[model_col] if model_col < len(row_a) else "")
        nb = _norm_model_name(row_b[model_col] if model_col < len(row_b) else "")
        score_a = (len(na), sum(1 for c in row_a if str(c or "").strip()), sum(len(str(c or "")) for c in row_a))
        score_b = (len(nb), sum(1 for c in row_b if str(c or "").strip()), sum(len(str(c or "")) for c in row_b))
        primary, secondary = (list(row_a), row_b) if score_a >= score_b else (list(row_b), row_a)
        max_len = max(len(primary), len(secondary))
        while len(primary) < max_len:
            primary.append("")
        for ci in range(max_len):
            p_val = str(primary[ci] if ci < len(primary) else "").strip()
            s_val = str(secondary[ci] if ci < len(secondary) else "").strip()
            if not p_val and s_val:
                primary[ci] = s_val
            elif ci != model_col and ci != cap_col and len(s_val) > len(p_val) and not re.match(r"^\d+(\.\d+)?$", s_val):
                primary[ci] = s_val
        return primary

    # 记录原有刻意留空的告警单元格（双模型分歧留空 / 防串行留空），清洗阶段严禁覆盖或丢弃这些行
    orig_protected_model_rows: set = {
        int(w["row"])
        for w in (warnings or [])
        if str(w.get("col", "")).isdigit()
        and int(w["col"]) == model_col
        and _is_protected_blank_warning(w.get("reason", ""))
    }
    step1_protected: set = {step1_map[orig_r] for orig_r in orig_protected_model_rows if orig_r in step1_map}
    extra_step2_warnings: List[Dict[str, Any]] = []

    # 第二遍：去除切片交界处「缺型号但容量+价格与下一行完全相同」的残缺行 + 多层嵌套合并列向下继承
    # 铁律（「宁愿留空也不瞎填」）：若当前空机型行带有不同网码族的新编码（如 A7070 -> C5560），严禁套用上一行机型名！
    step2_rows: List[List[str]] = []
    step2_map: Dict[int, int] = {}
    last_section = None
    last_model = ""
    last_code = ""
    last_metas: Dict[int, str] = {}

    for idx, r in enumerate(step1_rows):
        cur_sec = r[sec_col].strip() if sec_col is not None and sec_col < len(r) else ""
        if cur_sec != last_section:
            last_section = cur_sec
            last_model = ""
            last_code = ""
            last_metas = {}

        m_val = r[model_col].strip() if model_col < len(r) else ""
        c_val = r[cap_col].strip() if cap_col is not None and cap_col < len(r) else ""
        cur_code_val = r[code_col].strip() if code_col is not None and code_col < len(r) else ""

        # 汇总行（合计/总计/小计）原样保留，绝不套用上一行机型名也不丢弃
        if _is_summary_row(r):
            step2_map[idx] = len(step2_rows)
            step2_rows.append(r)
            continue

        if not m_val:
            # 若为双模型分歧刻意留空的保护行，原样保留空机型并打断向下继承
            if idx in step1_protected:
                last_model = ""
                if cur_code_val:
                    last_code = cur_code_val
                step2_map[idx] = len(step2_rows)
                step2_rows.append(r)
                continue

            # 检查是否与后 1~2 行（带完整机型）容量+价格重复
            p_sig = _price_sig(r)
            is_orphan_dup = False
            if p_sig:
                for ahead in range(idx + 1, min(len(step1_rows), idx + 3)):
                    nr = step1_rows[ahead]
                    nm = nr[model_col].strip() if model_col < len(nr) else ""
                    nc = nr[cap_col].strip() if cap_col is not None and cap_col < len(nr) else ""
                    if nm and (cap_col is None or not c_val or nc == c_val) and _price_sig(nr) == p_sig:
                        is_orphan_dup = True
                        break
            if is_orphan_dup:
                continue

            # 关键防瞎填护栏：若当前行有新的不同网码族（如 C5560 紧接 A7070），严禁继承上一行机型，留空并标黄！
            if cur_code_val and last_code and not _codes_same_family(cur_code_val, last_code):
                if c_val or p_sig:
                    last_model = ""
                    last_code = cur_code_val
                    last_metas = {}
                    new_s2_idx = len(step2_rows)
                    step2_map[idx] = new_s2_idx
                    step2_rows.append(r)
                    extra_step2_warnings.append(
                        {
                            "step2_row": new_s2_idx,
                            "col": model_col,
                            "reason": f"防错填拦截：网码已切换为「{cur_code_val}」但机型未识别，已留空请对照原图补录",
                        }
                    )
                continue

            # 若前面同一区块已有合法机型，且本行有容量或价格 -> 继承合并单元格的机型名与嵌套子列
            if last_model and (c_val or p_sig):
                r[model_col] = last_model
                if code_col is not None and code_col < len(r):
                    if cur_code_val:
                        last_code = cur_code_val
                    elif last_code:
                        r[code_col] = last_code
                for mc in meta_cols_list:
                    if mc < len(r):
                        if r[mc].strip():
                            last_metas[mc] = r[mc].strip()
                        elif last_metas.get(mc):
                            r[mc] = last_metas[mc]
            else:
                # 若虽无前序机型但带有明确网码+容量/价格，同样保留留空并标黄，避免静默丢行
                if cur_code_val and (c_val or p_sig):
                    last_code = cur_code_val
                    new_s2_idx = len(step2_rows)
                    step2_map[idx] = new_s2_idx
                    step2_rows.append(r)
                    extra_step2_warnings.append(
                        {
                            "step2_row": new_s2_idx,
                            "col": model_col,
                            "reason": f"防错填拦截：网码「{cur_code_val}」对应机型未识别，已留空请对照原图补录",
                        }
                    )
                continue
        else:
            if _norm_model_name(m_val) != _norm_model_name(last_model):
                last_code = ""
                last_metas = {}
            last_model = m_val
            if code_col is not None and code_col < len(r):
                if cur_code_val:
                    last_code = cur_code_val
                elif last_code:
                    r[code_col] = last_code
            for mc in meta_cols_list:
                if mc < len(r):
                    if r[mc].strip():
                        last_metas[mc] = r[mc].strip()
                    elif last_metas.get(mc):
                        r[mc] = last_metas[mc]

        step2_map[idx] = len(step2_rows)
        step2_rows.append(r)

    # 第三遍：智能消除相邻 1~6 行内的「同容量 + 同价格序列 + 截断/兼容机型名」重复行
    # 典型案例：切片缝把 17pro 截断成 17，或把 13 PRO 的 512/1T 错标成 13 / A2634
    # 注意：受保护的刻意留空行（双模型分歧/跨族防串行留空）以及不同网络型号编码的独立机型绝不误合并！
    step2_protected: set = {step2_map[s1_i] for s1_i in step1_protected if s1_i in step2_map} | {
        int(ew["step2_row"]) for ew in extra_step2_warnings
    }
    cleaned_rows: List[List[str]] = []
    cleaned_protected: set = set()
    step3_map: Dict[int, int] = {}
    for idx, r in enumerate(step2_rows):
        m_cur = r[model_col].strip() if model_col < len(r) else ""
        c_cur = _norm_cap_str(r[cap_col]) if cap_col is not None and cap_col < len(r) else ""
        code_cur = (
            re.sub(r"\s+", "", str(r[code_col] or "")).lower()
            if code_col is not None and code_col < len(r)
            else ""
        )
        p_cur = _price_sig(r)
        is_cur_protected = idx in step2_protected

        merged_into: Optional[int] = None
        if p_cur and not is_cur_protected:
            search_start = max(0, len(cleaned_rows) - 6)
            for prev_i in range(len(cleaned_rows) - 1, search_start - 1, -1):
                if prev_i in cleaned_protected:
                    continue
                pr = cleaned_rows[prev_i]
                m_prev = pr[model_col].strip() if model_col < len(pr) else ""
                c_prev = _norm_cap_str(pr[cap_col]) if cap_col is not None and cap_col < len(pr) else ""
                code_prev = (
                    re.sub(r"\s+", "", str(pr[code_col] or "")).lower()
                    if code_col is not None and code_col < len(pr)
                    else ""
                )
                p_prev = _price_sig(pr)
                same_cap = (cap_col is None) or (bool(c_cur) and c_cur == c_prev)
                if code_cur and code_prev and code_cur != code_prev:
                    continue
                if same_cap and p_prev == p_cur and _models_compatible(m_prev, m_cur):
                    cleaned_rows[prev_i] = _merge_dup_pair(pr, r)
                    merged_into = prev_i
                    break

        if merged_into is not None:
            step3_map[idx] = merged_into
        else:
            new_c_idx = len(cleaned_rows)
            step3_map[idx] = new_c_idx
            cleaned_rows.append(r)
            if is_cur_protected:
                cleaned_protected.add(new_c_idx)

    new_warnings: List[Dict[str, Any]] = []
    for w in (warnings or []):
        try:
            orig_r = int(w["row"])
            if orig_r in step1_map and step1_map[orig_r] in step2_map and step2_map[step1_map[orig_r]] in step3_map:
                new_warnings.append(
                    {
                        "row": step3_map[step2_map[step1_map[orig_r]]],
                        "col": int(w["col"]),
                        "reason": str(w.get("reason") or ""),
                    }
                )
        except Exception:
            continue

    for ew in extra_step2_warnings:
        s2_r = ew["step2_row"]
        if s2_r in step3_map:
            new_warnings.append({"row": step3_map[s2_r], "col": int(ew["col"]), "reason": ew["reason"]})

    return cleaned_rows, new_warnings


def _build_demo_result(filename: str, mode: str) -> Dict[str, Any]:
    """R5 容错兜底：未配置 GEMINI_API_KEY 或选择演示模式时返回高保真结构化示例表。"""
    lower = (filename or "").lower()
    if "finance" in lower or "revenue" in lower or "财务" in lower or "营收" in lower:
        raw = {
            "title": "Q3 区域营收与毛利率汇总表（演示提取）",
            "plain_text": "",
            "sheets": [
                {
                    "name": "Q3区域营收汇总",
                    "headers": ["区域中心", "产品线", "订单量", "含税营收", "同比增长", "毛利率", "备注"],
                    "col_types": ["text", "text", "number", "currency", "percent", "percent", "text"],
                    "rows": [
                        ["华东大区", "企业版 SaaS", "428", "¥1,284,000.00", "24.6%", "68.5%", "超额达成"],
                        ["华南大区", "AI 视觉 API", "315", "¥892,500.00", "31.2%", "72.1%", "新签头部客户 3 家"],
                        ["华北大区", "私有化部署", "112", "¥1,640,000.00", "12.4%", "54.8%", "二期交付中"],
                        ["西南大区", "企业版 SaaS", "196", "¥518,400.00", "18.9%", "66.0%", ""],
                        ["海外亚太", "Pro 订阅包", "640", "¥768,000.00", "45.8%", "79.4%", "重点增长区"],
                    ],
                    "merges": [],
                    "warnings": [
                        {"row": 3, "col": 6, "reason": "演示提示：该单元格原图为空，可双击补充备注"}
                    ],
                }
            ],
        }
    else:
        raw = {
            "title": "智能仓储库存盘点表（演示提取）",
            "plain_text": "",
            "sheets": [
                {
                    "name": "库存盘点明细",
                    "headers": ["SKU 编码", "商品名称", "品类", "当前库存", "安全阈值", "单价", "库存状态"],
                    "col_types": ["text", "text", "text", "number", "number", "currency", "text"],
                    "rows": [
                        ["001042", "Pro 无线机械键盘", "外设配件", "184", "50", "¥499.00", "充足"],
                        ["001089", "4K 27寸高刷显示器", "显示设备", "36", "40", "¥2,199.00", "低于阈值"],
                        ["002105", "雷电4 扩展坞 12合1", "连接器", "92", "30", "¥689.00", "充足"],
                        ["002418", "人体工学升降桌支架", "办公家具", "18", "25", "¥1,280.00", "建议补货"],
                        ["003007", "降噪会议麦克风阵列", "音频设备", "64", "20", "¥899.00", "充足"],
                        ["003152", "Type-C 140W 氮化镓电源", "电源配件", "245", "60", "¥269.00", "充足"],
                    ],
                    "merges": [],
                    "warnings": [
                        {"row": 1, "col": 3, "reason": "演示提示：当前库存 36 低于安全阈值 40，可在线校对高亮格"}
                    ],
                }
            ],
        }
    normalized = normalize_table_grid(raw, fallback_name=Path(filename or "sample").stem)
    raw_sheets = normalized["sheets"]
    quote_title = _pick_quote_title(normalized["title"], Path(filename or "sample").stem)
    merged_sheet = merge_sheets_into_single_quotation(raw_sheets, quote_title, Path(filename or "sample").stem)
    normalized["quote_title"] = quote_title
    normalized["document_notes"] = normalized.get("document_notes") or "演示规则备注：当前展示结构化示例报价单，可修改任意单元格后导出 .xlsx。"
    normalized["raw_sheets"] = raw_sheets
    normalized["merged_sheet"] = merged_sheet
    normalized["sheets"] = [merged_sheet] if mode != "text" else raw_sheets[:1]
    normalized["engine"] = "demo"
    normalized["is_demo"] = True
    normalized["notice"] = "当前为演示提取模式（未配置 GEMINI_API_KEY）。你仍可完整体验在线校对编辑与真实 .xlsx / .csv 导出。"
    return normalized


def _extract_text_from_interactions_response(data: Dict[str, Any]) -> str:
    """兼容 Gemini Interactions API 响应结构（steps / outputs / output_text）。"""
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"]

    texts: List[str] = []
    for step in reversed(data.get("steps") or []):
        if step.get("type") == "model_output":
            for item in (step.get("content") or []):
                if item.get("type") == "text" and item.get("text"):
                    texts.append(item["text"])
            if texts:
                break
    if texts:
        return "\n".join(reversed(texts))

    for out in (data.get("outputs") or []):
        if out.get("type") == "text" and out.get("text"):
            texts.append(out["text"])
    return "\n".join(texts)


def _row_signature(row: List[str]) -> str:
    return "|".join(re.sub(r"\s+", "", str(c or "")).lower() for c in row)


def _row_key_signature(row: List[str]) -> str:
    """用前两列（如 型号 + 容量）作为边界去重辅助键。"""
    parts = [re.sub(r"\s+", "", str(c or "")).lower() for c in row[:2]]
    return "|".join(parts)


def _row_price_values(row: List[str]) -> tuple:
    """提取行内从第 3 列起的数值价格序列，用于跨切片容错去重（抗机型名截断）。"""
    vals: List[str] = []
    for val in row[2:]:
        v = re.sub(r"[¥$€£,\s]", "", str(val or ""))
        if re.match(r"^\d+(\.\d+)?$", v):
            vals.append(v)
    return tuple(vals)


def _rows_overlap_equivalent(
    prev_row: List[str],
    next_row: List[str],
    prev_roles: Optional[Dict[str, Any]] = None,
    next_roles: Optional[Dict[str, Any]] = None,
    allow_diff_code: bool = False,
    is_quotation: bool = True,
) -> bool:
    """
    动态列感知判断两段切片重叠处的两行是否为同一条数据：
    - 通用表格模式（is_quotation=False）：仅在整行完全一致时判定为重叠重复，杜绝误删同金额相邻流水/明细行；
    - 报价单模式（is_quotation=True）：兼容多前置列结构与切缝短机型截断/容量回跳幽灵行。
    """
    if _row_signature(prev_row) == _row_signature(next_row):
        return True
    if not is_quotation:
        return False
    m_a, cap_a, p_prices = _extract_row_semantics(prev_row, prev_roles)
    m_b, cap_b, n_prices = _extract_row_semantics(next_row, next_roles)

    if m_a and m_a == m_b and cap_a and cap_a == cap_b:
        if not p_prices or not n_prices or p_prices == n_prices:
            return True

    # 切缝处容量一致 + 价格序列（>=2 列）完全一致：
    # 仅在机型名兼容（如 17 vs 17pro、13 vs 13 PRO、同机型或空机型）或确认容量回跳幽灵行时判定为切缝重复；
    # 严禁把两款非兼容独立机型（如 Y35 5G vs Y35m 5G，或 小米12S Pro 5G vs 小米12S 5G）仅因同价而误合并！
    if cap_a and cap_a == cap_b and p_prices and p_prices == n_prices:
        if _models_compatible_norm(m_a, m_b) or allow_diff_code:
            # 若编码列存在且双方均有非空不同编码，除非已确认是容量回跳幽灵行（allow_diff_code=True），否则不误删同价异款
            if not allow_diff_code:
                c_col_a = prev_roles.get("code_col") if prev_roles else None
                c_col_b = next_roles.get("code_col") if next_roles else None
                if c_col_a is not None and c_col_b is not None and c_col_a < len(prev_row) and c_col_b < len(next_row):
                    ca = re.sub(r"\s+", "", str(prev_row[c_col_a] or "")).lower()
                    cb = re.sub(r"\s+", "", str(next_row[c_col_b] or "")).lower()
                    if ca and cb and ca != cb:
                        return False
            return True
    return False


def _merge_overlap_row(
    prev_row: List[str],
    next_row: List[str],
    prev_roles: Optional[Dict[str, Any]] = None,
    next_roles: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """重叠行合并：保留更完整的机型名与对应编码，并补齐因切片裁剪丢失的右侧单元格内容。"""
    m_a, _, _ = _extract_row_semantics(prev_row, prev_roles)
    m_b, _, _ = _extract_row_semantics(next_row, next_roles)
    score_a = (len(m_a), sum(1 for c in prev_row if str(c or "").strip()), sum(len(str(c or "")) for c in prev_row))
    score_b = (len(m_b), sum(1 for c in next_row if str(c or "").strip()), sum(len(str(c or "")) for c in next_row))
    primary, secondary = (list(prev_row), next_row) if score_a >= score_b else (list(next_row), prev_row)
    max_len = max(len(primary), len(secondary))
    while len(primary) < max_len:
        primary.append("")
    for i in range(max_len):
        pv = str(primary[i] if i < len(primary) else "").strip()
        sv = str(secondary[i] if i < len(secondary) else "").strip()
        if not pv and sv:
            primary[i] = sv
    return primary


def _dedupe_boundary_overlap(
    prev_rows: List[List[str]],
    next_rows: List[List[str]],
    prev_headers: Optional[List[str]] = None,
    next_headers: Optional[List[str]] = None,
    max_window: int = 10,
    is_quotation: bool = True,
) -> int:
    """
    计算前后两段切片在重叠区域的重复行数（动态适配任意前置列布局）：
    - 报价单模式：若匹配到切缝残缺行或跨机型幽灵行，自动保留更完整的一方并跳过 next_rows 起始重复行；
    - 通用表格模式：仅对整行完全一致的切片重叠行去重，避免误删同金额相邻流水/明细。
    """
    if not prev_rows or not next_rows:
        return 0
    prev_roles = _detect_column_roles(prev_headers) if prev_headers else None
    next_roles = _detect_column_roles(next_headers) if next_headers else prev_roles

    prev_tail_start = max(0, len(prev_rows) - max_window)
    prev_tail = prev_rows[prev_tail_start:]
    next_head = next_rows[:max_window]

    # 检测 next_head 中是否存在「容量回跳幽灵行」（仅报价单模式启用）
    next_has_smaller_cap: List[bool] = [False] * len(next_head)
    if is_quotation:
        next_sems = [_extract_row_semantics(r, next_roles) for r in next_rows[: max_window + 6]]
        for i in range(len(next_head)):
            m_i, c_i, _ = next_sems[i]
            gb_i = _cap_to_gb(c_i)
            if not m_i or gb_i is None:
                continue
            for j in range(i + 1, min(len(next_sems), i + 6)):
                m_j, c_j, _ = next_sems[j]
                if m_j != m_i:
                    break
                gb_j = _cap_to_gb(c_j)
                if gb_j is not None and gb_j < gb_i:
                    next_has_smaller_cap[i] = True
                    break

    max_k = min(len(prev_tail), len(next_head))
    for k in range(max_k, 0, -1):
        if all(
            _rows_overlap_equivalent(
                prev_tail[-k + i],
                next_head[i],
                prev_roles,
                next_roles,
                allow_diff_code=next_has_smaller_cap[i],
                is_quotation=is_quotation,
            )
            for i in range(k)
        ):
            for i in range(k):
                target_idx = len(prev_rows) - k + i
                prev_rows[target_idx] = _merge_overlap_row(prev_rows[target_idx], next_head[i], prev_roles, next_roles)
            return k

    skip = 0
    for idx in range(min(6, len(next_head))):
        matched_prev: Optional[int] = None
        for p_offset in range(len(prev_tail) - 1, -1, -1):
            if _rows_overlap_equivalent(
                prev_tail[p_offset],
                next_head[idx],
                prev_roles,
                next_roles,
                allow_diff_code=next_has_smaller_cap[idx],
                is_quotation=is_quotation,
            ):
                matched_prev = prev_tail_start + p_offset
                break
        if matched_prev is not None:
            prev_rows[matched_prev] = _merge_overlap_row(prev_rows[matched_prev], next_head[idx], prev_roles, next_roles)
            skip += 1
        else:
            break
    return skip


def merge_quotation_slices(
    slice_results: List[Dict[str, Any]],
    filename: str,
    merge_per_image: bool = True,
    doc_type: str = "auto",
) -> Dict[str, Any]:
    """将同一张超长表格切分后的多段识别结果无损拼接、重叠去重，并自适应报价单与通用表格双模式。"""
    stem = Path(filename or "extracted").stem
    if not slice_results:
        return _build_demo_result(filename, "excel")
    if len(slice_results) == 1:
        return slice_results[0]

    quote_title = stem[:28]
    for sr in slice_results:
        cand = str(sr.get("quote_title") or "").strip()
        if cand and cand != stem[:28]:
            quote_title = cand[:28]
            break

    notes_parts: List[str] = []
    for sr in slice_results:
        n = str(sr.get("document_notes") or "").strip()
        if n and n not in notes_parts:
            notes_parts.append(n)

    # 先基于所有切片综合判定是否为报价单（避免通用长表误用报价单模糊去重）
    all_slice_sheets = [sh for sr in slice_results for sh in (sr.get("raw_sheets") or sr.get("sheets") or [])]
    is_quote_doc = any(
        _is_quotation_sheet(sh.get("headers") or [], sh.get("rows") or [], quote_title or sh.get("name", ""), doc_type)
        for sh in all_slice_sheets
    )

    combined_raw_sheets: List[Dict[str, Any]] = []
    for sr in slice_results:
        for sh in (sr.get("raw_sheets") or sr.get("sheets") or []):
            sh_copy = json.loads(json.dumps(sh))
            if not combined_raw_sheets:
                combined_raw_sheets.append(sh_copy)
                continue
            prev = combined_raw_sheets[-1]
            prev_name = str(prev.get("name", "")).strip()
            cur_name = str(sh_copy.get("name", "")).strip()
            same_name = prev_name == cur_name
            same_headers = prev.get("headers") == sh_copy.get("headers")
            is_continuation = cur_name.lower() in {"", "sheet1", "table", "continuation", "续表"}
            if same_name or same_headers or is_continuation:
                prev_rows = prev.get("rows") or []
                next_rows = sh_copy.get("rows") or []
                skip = _dedupe_boundary_overlap(
                    prev_rows, next_rows, prev.get("headers"), sh_copy.get("headers"), is_quotation=is_quote_doc
                )
                trimmed_next = next_rows[skip:]
                if same_headers:
                    offset = len(prev_rows)
                    prev["rows"] = prev_rows + trimmed_next
                    for w in (sh_copy.get("warnings") or []):
                        wr = int(w.get("row", -1)) - skip
                        if wr >= 0:
                            prev.setdefault("warnings", []).append(
                                {"row": offset + wr, "col": int(w.get("col", 0)), "reason": w.get("reason", "")}
                            )
                    continue
                sh_copy["rows"] = trimmed_next
                if skip > 0 and sh_copy.get("warnings"):
                    sh_copy["warnings"] = [
                        {"row": int(w.get("row", -1)) - skip, "col": int(w.get("col", 0)), "reason": w.get("reason", "")}
                        for w in sh_copy["warnings"]
                        if int(w.get("row", -1)) - skip >= 0
                    ]
            else:
                skip = _dedupe_boundary_overlap(
                    prev.get("rows") or [],
                    sh_copy.get("rows") or [],
                    prev.get("headers"),
                    sh_copy.get("headers"),
                    is_quotation=is_quote_doc,
                )
                sh_copy["rows"] = (sh_copy.get("rows") or [])[skip:]
                if skip > 0 and sh_copy.get("warnings"):
                    sh_copy["warnings"] = [
                        {"row": int(w.get("row", -1)) - skip, "col": int(w.get("col", 0)), "reason": w.get("reason", "")}
                        for w in sh_copy["warnings"]
                        if int(w.get("row", -1)) - skip >= 0
                    ]
            if sh_copy.get("rows"):
                combined_raw_sheets.append(sh_copy)

    for rsh in combined_raw_sheets:
        rsh_is_quote = _is_quotation_sheet(
            rsh.get("headers") or [], rsh.get("rows") or [], quote_title or rsh.get("name", ""), doc_type
        )
        r_rows, r_warns = _clean_quotation_rows(
            rsh["headers"], rsh["rows"], rsh.get("warnings") or [], is_quotation=rsh_is_quote
        )
        rsh["rows"] = r_rows
        rsh["warnings"] = r_warns
        rsh.setdefault("section_header_rows", [])
        rsh["style_preset"] = "quotation" if rsh_is_quote else "general"

    merged_sheet = merge_sheets_into_single_quotation(combined_raw_sheets, quote_title, stem, doc_type=doc_type)
    suggest_split = _should_keep_multi_sheets_separate(combined_raw_sheets, doc_type=doc_type)
    detected_doc_type = "quotation" if is_quote_doc else "general"

    last_res = slice_results[-1]
    plain_lines = []
    if merged_sheet.get("headers"):
        plain_lines.append(" | ".join(merged_sheet["headers"]))
        plain_lines.append(" | ".join(["---"] * len(merged_sheet["headers"])))
        for r in merged_sheet.get("rows") or []:
            plain_lines.append(" | ".join(r))

    return {
        "title": quote_title,
        "quote_title": quote_title,
        "document_notes": " | ".join(notes_parts),
        "raw_sheets": combined_raw_sheets,
        "merged_sheet": merged_sheet,
        "sheets": [merged_sheet] if (merge_per_image and not suggest_split) else combined_raw_sheets,
        "plain_text": "\n".join(plain_lines),
        "engine": last_res.get("engine", DEFAULT_GEMINI_MODEL),
        "is_demo": bool(last_res.get("is_demo", False)),
        "detected_doc_type": detected_doc_type,
        "suggest_split_sheets": suggest_split,
        "notice": f"长图已分 {len(slice_results)} 段高清切片提取并去重合并（共 {len(merged_sheet.get('rows') or [])} 行 · {'报价单模式' if is_quote_doc else '通用表格模式'}）",
    }


def _parse_model_json(text_out: str) -> Dict[str, Any]:
    """容错解析模型 JSON：自动剥离 Markdown 代码块、外围说明文字与尾随逗号。"""
    raw = (text_out or "").strip()
    if not raw:
        raise ValueError("模型返回空响应")
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        pass
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = cleaned[first_brace : last_brace + 1]
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
        return json.loads(repaired, strict=False)
    repaired = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return json.loads(repaired, strict=False)


def _norm_compare_token(val: str) -> str:
    """宽松比对键：忽略空格、大小写、全半角括号与连接符/斜杠，避免「红魔6」vs「红魔 6」、「MRO-W00 / MRO-W10」vs「MRO-W00 MRO-W10」等无害微差误报分歧。"""
    s = str(val or "").strip().lower()
    s = s.replace("（", "(").replace("）", ")").replace("＋", "+").replace("／", "/")
    return re.sub(r"[\s\-_/·]+", "", s)


def _norm_cap_compare(val: str) -> str:
    """容量核验比对键：忽略空格/大小写及 256 vs 256G/GB、1T vs 1TB 写法差异，但严格区分 16+512 vs 16+1TB、8+128 vs 8+256。"""
    s = _norm_cap_str(val)
    s = s.replace("（", "(").replace("）", ")").replace("＋", "+").replace("／", "/")
    # 统一内部 tb/gb 别名（如 16+1tb尊享版 -> 16+1t尊享版，12+256gb -> 12+256）
    s = re.sub(r"(\d+(?:\.\d+)?)tb", r"\1t", s)
    s = re.sub(r"(\d+)gb?", r"\1", s)
    return re.sub(r"[\s\-_]+", "", s)


def _norm_price_token(val: str) -> str:
    return re.sub(r"[¥￥$€£,\s]", "", str(val or "").strip())


_API_REJECTS_GEN_CONFIG = False
_API_MAX_RESOLUTION = "ultra_high"


def _call_single_gemini_model(
    model_name: str,
    instruction: str,
    b64_data: str,
    mime_type: str,
    headers: Dict[str, str],
    api_key: str,
    req_timeout: int,
    target_resolution: str = "ultra_high",
    deadline: Optional[float] = None,
) -> Tuple[Optional[Dict[str, Any]], str, bool]:
    """
    调用单个 Gemini 模型（锁定 temperature=0.0 降低随机幻觉，支持 generation_config 与 resolution 降级兼容）。
    严格受 deadline 墙钟预算约束，杜绝超时撞穿 Vercel 60s 导致 Cloudflare 520。
    返回 (parsed_dict_or_None, error_msg, is_fatal_auth_or_4xx)。
    """
    global _API_REJECTS_GEN_CONFIG, _API_MAX_RESOLUTION

    def _effective_timeout() -> Optional[Tuple[float, float]]:
        if deadline is None:
            return (6.0, float(max(5, req_timeout)))
        rem = deadline - time.monotonic()
        if rem < 4.0:
            return None
        read_t = min(float(max(5, req_timeout)), rem)
        return (min(6.0, max(2.0, read_t * 0.4)), read_t)

    eff_res = _API_MAX_RESOLUTION if target_resolution == "ultra_high" else target_resolution
    image_block: Dict[str, Any] = {
        "type": "image",
        "data": b64_data,
        "mime_type": mime_type,
    }
    if eff_res:
        image_block["resolution"] = eff_res

    payload: Dict[str, Any] = {
        "model": model_name,
        "store": False,
        "input": [
            {"type": "text", "text": instruction},
            image_block,
        ],
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": TABLE_EXTRACTION_SCHEMA,
        },
    }
    if not _API_REJECTS_GEN_CONFIG:
        payload["generation_config"] = {"temperature": 0.0}

    try:
        t_pair = _effective_timeout()
        if t_pair is None:
            return None, f"Gemini 模型 {model_name} 接近单次安全耗时上限，已快速中止交由重试", False
        resp = requests.post(
            GEMINI_INTERACTIONS_URL,
            json=payload,
            headers=headers,
            timeout=t_pair,
        )
        # 兼容不接受 generation_config 或 resolution 的端点，按需降级重试并缓存能力避免重复 400 往返
        for _ in range(3):
            if resp.status_code != 400:
                break
            err_low = resp.text.lower()
            retried = False
            if ("generation_config" in err_low or "temperature" in err_low) and "generation_config" in payload:
                _API_REJECTS_GEN_CONFIG = True
                payload.pop("generation_config", None)
                retried = True
            elif "resolution" in err_low:
                if image_block.get("resolution") == "ultra_high":
                    _API_MAX_RESOLUTION = "high"
                    image_block["resolution"] = "high"
                    retried = True
                elif "resolution" in image_block:
                    _API_MAX_RESOLUTION = ""
                    image_block.pop("resolution", None)
                    retried = True
            if not retried:
                break
            t_pair = _effective_timeout()
            if t_pair is None:
                return None, f"Gemini 模型 {model_name} 接近单次安全耗时上限，已快速中止交由重试", False
            resp = requests.post(GEMINI_INTERACTIONS_URL, json=payload, headers=headers, timeout=t_pair)
    except requests.RequestException as exc:
        return None, f"连接 Gemini 视觉识别服务超时或网络异常：{type(exc).__name__}", False

    if resp.status_code in (401, 403):
        return None, "GEMINI_API_KEY 鉴权失败，请检查 Vercel 或 .env 中的密钥配置是否正确。", True
    if resp.status_code in (429, 500, 502, 503, 504):
        err_preview = resp.text[:240].replace(api_key, "***")
        return None, f"Gemini 模型 {model_name} 暂不可用 (HTTP {resp.status_code})：{err_preview}", False
    if resp.status_code != 200:
        err_preview = resp.text[:240].replace(api_key, "***")
        return None, f"Gemini 视觉识别接口返回异常 (HTTP {resp.status_code})：{err_preview}", True

    try:
        raw_json = resp.json()
        text_out = _extract_text_from_interactions_response(raw_json).strip()
        candidate_parsed = _parse_model_json(text_out)
        sheets_val = candidate_parsed.get("sheets")
        has_rows = isinstance(sheets_val, list) and any(
            isinstance(s, dict) and isinstance(s.get("rows"), list) and len(s["rows"]) > 0
            for s in sheets_val
        )
        if not has_rows:
            return None, f"Gemini 模型 {model_name} 返回空表格，正在切换模型重试", False
        return candidate_parsed, "", False
    except Exception as exc:
        return None, f"Gemini 模型 {model_name} 返回结构解析异常（{type(exc).__name__}），正在自动重试", False


def _cross_verify_dual_sheets(
    primary_sheets: List[Dict[str, Any]],
    secondary_sheets: List[Dict[str, Any]],
    primary_model: str,
    secondary_model: str,
) -> int:
    """
    双模型逐行逐格交叉核验（「宁愿留空也不瞎填」核心）：
    - 按价格签名与局部行序精准对齐主副模型识别结果；
    - 忽略纯空格/大小写/单位别名微差（如 红魔6 vs 红魔 6、1TB vs 1T、16+512尊享版 vs 16+512 尊享版）；
    - 若主副模型在「型号 / 网络型号 / 容量 / 价格」出现实质分歧（如 16+1TB vs 16+512、8+128 vs 8+256、A80 vs A90 5G），
      直接将该单元格置空 '' 并写入明确的分歧告警（导出 Excel 自动标黄 + 批注列出双方读数）。
    返回共拦截并留空的实质分歧单元格数。
    """
    # 收集副模型全部有效数据行（带各自列角色）
    sec_entries: List[Dict[str, Any]] = []
    for ssh in secondary_sheets:
        s_hdrs = ssh.get("headers") or []
        s_roles = _detect_column_roles(s_hdrs)
        s_m_col = s_roles.get("model_col")
        s_cd_col = s_roles.get("code_col")
        s_cap_col = s_roles.get("cap_col")
        s_p_cols: List[int] = s_roles.get("price_cols") or []
        for s_row in (ssh.get("rows") or []):
            s_prices = tuple(
                _norm_price_token(s_row[c])
                for c in s_p_cols
                if c < len(s_row) and _norm_price_token(s_row[c])
            )
            sec_entries.append(
                {
                    "row": s_row,
                    "roles": s_roles,
                    "model": str(s_row[s_m_col]).strip() if (s_m_col is not None and s_m_col < len(s_row)) else "",
                    "code": str(s_row[s_cd_col]).strip() if (s_cd_col is not None and s_cd_col < len(s_row)) else "",
                    "cap": str(s_row[s_cap_col]).strip() if (s_cap_col is not None and s_cap_col < len(s_row)) else "",
                    "prices": s_prices,
                    "price_list": [_norm_price_token(s_row[c]) if c < len(s_row) else "" for c in s_p_cols],
                }
            )

    total_primary_rows = sum(len(sh.get("rows") or []) for sh in primary_sheets)
    if total_primary_rows == 0 or len(sec_entries) < max(1, int(total_primary_rows * 0.55)):
        # 若副模型疑似截断或漏大半内容，跳过交叉留空以免误伤完整主模型结果
        return 0

    used_sec: set = set()
    conflict_count = 0
    global_p_idx = 0

    for psh in primary_sheets:
        p_hdrs = psh.get("headers") or []
        p_roles = _detect_column_roles(p_hdrs)
        p_m_col = p_roles.get("model_col")
        p_cd_col = p_roles.get("code_col")
        p_cap_col = p_roles.get("cap_col")
        p_p_cols: List[int] = p_roles.get("price_cols") or []
        p_rows = psh.get("rows") or []
        p_warns = psh.setdefault("warnings", [])
        existing_warn_pos = {(int(w.get("row", -1)), int(w.get("col", -1))) for w in p_warns}

        for r_i, p_row in enumerate(p_rows):
            cur_g_idx = global_p_idx
            global_p_idx += 1

            p_m = str(p_row[p_m_col]).strip() if (p_m_col is not None and p_m_col < len(p_row)) else ""
            p_cd = str(p_row[p_cd_col]).strip() if (p_cd_col is not None and p_cd_col < len(p_row)) else ""
            p_cap = str(p_row[p_cap_col]).strip() if (p_cap_col is not None and p_cap_col < len(p_row)) else ""
            p_prices = tuple(
                _norm_price_token(p_row[c])
                for c in p_p_cols
                if c < len(p_row) and _norm_price_token(p_row[c])
            )
            p_price_list = [_norm_price_token(p_row[c]) if c < len(p_row) else "" for c in p_p_cols]

            # 在局部窗口内寻找最匹配的副模型行（优先价格序列完全一致，其次同网码/同机型+同容量）
            lo = max(0, cur_g_idx - 8)
            hi = min(len(sec_entries), cur_g_idx + 9)
            best_s_idx: Optional[int] = None
            best_score = -1

            p_m_norm = _norm_compare_token(p_m)
            p_cd_norm = _norm_compare_token(p_cd)
            p_cap_norm = _norm_cap_str(p_cap)

            for s_idx in range(lo, hi):
                if s_idx in used_sec:
                    continue
                se = sec_entries[s_idx]
                dist = abs(s_idx - cur_g_idx)
                score = 0
                if len(p_prices) >= 3 and se["prices"] == p_prices:
                    score += 100
                elif len(p_prices) >= 2 and se["prices"] == p_prices and dist <= 4:
                    score += 80
                elif len(p_price_list) >= 3 and len(se["price_list"]) == len(p_price_list):
                    same_p = sum(1 for a, b in zip(p_price_list, se["price_list"]) if a and a == b)
                    if same_p >= len(p_price_list) - 1 and dist <= 3:
                        score += 60

                if p_cd_norm and _norm_compare_token(se["code"]) == p_cd_norm:
                    score += 20
                if p_m_norm and _norm_compare_token(se["model"]) == p_m_norm:
                    score += 15
                if p_cap_norm and _norm_cap_str(se["cap"]) == p_cap_norm:
                    score += 15

                if score >= 80:
                    adj_score = score * 10 - dist
                    if adj_score > best_score:
                        best_score = adj_score
                        best_s_idx = s_idx

            if best_s_idx is None:
                continue
            used_sec.add(best_s_idx)
            se = sec_entries[best_s_idx]

            def _flag_conflict(col_i: int, label: str, v_primary: str, v_secondary: str) -> None:
                nonlocal conflict_count
                if col_i < len(p_row):
                    p_row[col_i] = ""
                if (r_i, col_i) not in existing_warn_pos:
                    existing_warn_pos.add((r_i, col_i))
                    p_warns.append(
                        {
                            "row": r_i,
                            "col": col_i,
                            "reason": f"双模型{label}分歧（{primary_model}: {v_primary or '空'} / {secondary_model}: {v_secondary or '空'}），已留空请对照原图确认",
                        }
                    )
                    conflict_count += 1

            # 1) 核验「型号」
            if p_m_col is not None and se["roles"].get("model_col") is not None:
                s_m = se["model"]
                if (p_m or s_m) and _norm_compare_token(p_m) != _norm_compare_token(s_m):
                    p_is_ph = bool(p_m) and _is_series_or_header_model(p_m)
                    s_is_ph = bool(s_m) and _is_series_or_header_model(s_m)
                    if s_is_ph and p_m and not p_is_ph:
                        # 副模型误将「A系列/W系列/C系列」分组表头填入型号列，采信主模型真实机型名，不误伤留空
                        pass
                    elif p_is_ph and s_m and not s_is_ph:
                        # 主模型误填「xxx系列」占位而副模型读出真实机型名，自动采纳真实机型名
                        if p_m_col < len(p_row):
                            p_row[p_m_col] = s_m
                    else:
                        # 实质型号分歧（如 14 PRO MAX vs 14 PRO、A80 vs A90 5G），一律留空并给出双候选供 1 秒核对
                        _flag_conflict(p_m_col, "型号", p_m, s_m)

            # 2) 核验「网络型号 / 具体型号 / 小型号」
            if p_cd_col is not None and se["roles"].get("code_col") is not None:
                s_cd = se["code"]
                if (p_cd or s_cd) and _norm_compare_token(p_cd) != _norm_compare_token(s_cd):
                    _flag_conflict(p_cd_col, "网络型号", p_cd, s_cd)

            # 3) 核验「容量」（严格捕获 16+1TB vs 16+512、8+128 vs 8+256，忽略空格与 G/GB/T/TB 写法差异）
            if p_cap_col is not None and se["roles"].get("cap_col") is not None:
                s_cap = se["cap"]
                if (p_cap or s_cap) and _norm_cap_compare(p_cap) != _norm_cap_compare(s_cap):
                    _flag_conflict(p_cap_col, "容量", p_cap, s_cap)

            # 4) 核验「价格列」（当列数一致且单格价格出现分歧）
            if len(p_p_cols) == len(se["price_list"]) and len(p_p_cols) >= 2:
                for p_col_pos, s_val in zip(p_p_cols, se["price_list"]):
                    p_val_raw = str(p_row[p_col_pos]).strip() if p_col_pos < len(p_row) else ""
                    p_val_n = _norm_price_token(p_val_raw)
                    if (p_val_n or s_val) and p_val_n != s_val:
                        _flag_conflict(p_col_pos, "价格", p_val_raw, s_val)

    return conflict_count


def extract_tables_from_image(
    image_bytes: bytes,
    filename: str,
    content_type: Optional[str] = None,
    mode: str = "excel",
    force_demo: bool = False,
    custom_hint: str = "",
    merge_per_image: bool = True,
    model_tier: str = "fast",
    prices_only: bool = False,
    doc_type: str = "auto",
    dual_verify: bool = False,
) -> Dict[str, Any]:
    """
    核心图像表格提取函数（双模式自适应 + 双模型并发交叉核验 + 高可靠加固版）：
    - 支持 doc_type='auto'（智能自动识别报价单/通用表格）、'quotation'（报价单专精）、'general'（通用多场景表格）；
    - 支持 dual_verify=True：并发调用 3.5-flash-lite 与 3.1-flash-lite（均锁 temperature=0）逐格交叉核验，
      凡型号/网络型号/容量/价格出现实质分歧一律留空并标黄批注（「宁愿留空也不瞎填」）；
    - 超时预算严格控制在 Vercel 60s 函数上限内，单模型异常时自动平滑降级兜底。
    """
    norm_doc_type = (doc_type or "auto").strip().lower()
    if norm_doc_type not in {"auto", "quotation", "general"}:
        norm_doc_type = "auto"
    effective_prices_only = bool(prices_only) and norm_doc_type != "general"

    api_key = _get_gemini_api_key()
    if force_demo or not api_key:
        demo_res = _build_demo_result(filename, mode)
        demo_res["detected_doc_type"] = "general"
        demo_res["suggest_split_sheets"] = False
        if not merge_per_image:
            demo_res["sheets"] = demo_res["raw_sheets"]
        return demo_res

    mime_type = normalize_mime_type(filename, content_type)
    b64_data = base64.b64encode(image_bytes).decode("ascii")
    env_model = _get_clean_env("GEMINI_MODEL")

    if model_tier == "accurate":
        raw_candidates = ["gemini-4-hydrogen", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        timeout_budget = [24, 16, 12]
    else:
        # 严格锁定极速稳定档：主备交替重试（去重后形成 主 -> 备 -> 主 的 3 次高可靠尝试）
        fast_primary = env_model if (env_model and "flash" in env_model.lower()) else DEFAULT_GEMINI_MODEL
        fast_backup = "gemini-3.1-flash-lite" if fast_primary != "gemini-3.1-flash-lite" else "gemini-3.5-flash-lite"
        raw_candidates = [fast_primary, fast_backup, fast_primary]
        timeout_budget = [22, 16, 12]

    candidate_models: List[str] = raw_candidates[:3]
    primary_model = candidate_models[0]

    if norm_doc_type == "general":
        instruction = (
            "You are an expert universal document-and-table OCR extraction engine (supporting financial statements, invoices, receipts, inventory sheets, attendance logs, schedules, BOMs, web/ERP screenshots, and general Chinese/English tables). "
            "CRITICAL: Extract EVERY SINGLE ROW, EVERY COLUMN, and EVERY distinct table visible in this image from top to bottom. "
            "NEVER omit columns (including remarks/notes/descriptions), NEVER omit summary/subtotal/total rows ('合计', '小计', '总计', 'Total'), NEVER output blank empty rows, and NEVER truncate the output. "
            "VISUAL HEADER ANCHOR RULE: If there is a bright RED horizontal divider bar near the top of the image, the strip ABOVE the red bar is a copied visual column-header reference—use it ONLY to keep exact column names and left-to-right column alignment, and put ONLY the data rows BELOW the red bar into `rows`. "
            "MULTI-TABLE SEPARATION RULE: If the image contains multiple structurally distinct tables (for example an invoice buyer/seller summary block at the top and an itemized line-items table below), output each distinct table as a separate item in `sheets` with a concise Chinese/English `name`. "
            "MERGED CELLS & TEXT FIDELITY: When a left-hand grouping column is vertically merged across multiple detail rows, repeat that group value onto each corresponding row (EXCEPT summary/total rows like '合计'/'小计', which must keep their own layout). Join wrapped single-cell titles on one line with spaces instead of literal '\\n'. "
            "NUMBER & CURRENCY FIDELITY: Preserve exact numbers, currency symbols and thousands separators (e.g. '¥1,284,000.00', '$499.50'), percentages ('24.6%'), dates ('2026-11-26'), formulas/ranges, and leading zeros (e.g. '001042'). Ignore diagonal watermarks. "
            "Put non-tabular page headers, footnotes, or disclaimers into `document_notes`. Set `title` to the main document title visible in the image. "
            "Every row in `rows` MUST have the exact same length as `headers` (use '' for blank cells), and set `col_types` accurately (`text`, `number`, `currency`, `percent`, `date`)."
        )
    else:
        right_col_rule = (
            "PRICES-ONLY MODE ENABLED (for quotation sheets): Extract ONLY the left identification columns (e.g. '型号', '网络型号', '小型号', '容量/内存') and all numeric price tier columns (e.g. '充新', '靓机', '小花', '大花', '外爆', '内爆可测'). COMPLETELY SKIP long rightmost deduction/remark text columns ('备注', '扣费说明', '扣费项', '蓝光/彩点') to maximize speed and numeric precision. (If this image is NOT a quotation sheet—e.g. a financial report, invoice, or general table—keep ALL columns faithfully.) "
            if effective_prices_only
            else "NEVER omit rightmost remark or deduction columns! Even if the rightmost columns contain dense multi-line Chinese rules (like '绿色-50 / 港澳版-150 / 像头水印-100 / 弹屏-450') or share a merged header ('扣费说明' / '备注'), ALWAYS include them faithfully as dedicated rightmost column(s). "
        )
        general_adapt_rule = (
            "GENERAL MULTI-SCENARIO ADAPTATION: If this image is a general non-quotation table (financial statement, invoice, BOM, inventory, schedule, attendance, ERP screenshot), faithfully keep ALL columns, preserve currency symbols and thousands separators (e.g. '¥1,284,000.00'), preserve all '合计/小计/总计' summary rows, and place structurally distinct tables as separate items in `sheets`. "
            if norm_doc_type == "auto"
            else ""
        )
        instruction = (
            "You are an expert document and quotation-sheet OCR extraction engine. "
            "CRITICAL: Extract EVERY SINGLE ROW and EVERY table/block visible in this image from the very top to the very bottom. "
            "NEVER omit rows, NEVER omit price cells, NEVER output empty blank rows, NEVER stop after the first section, and NEVER truncate the output. "
            + general_adapt_rule
            + "VISUAL HEADER ANCHOR RULE: If there is a bright RED horizontal divider bar near the top of the image, the strip ABOVE the red bar is a copied visual column-header reference from the top of the sheet—use it ONLY to keep exact column names and left-to-right column alignment, and put ONLY the data rows BELOW the red bar into `rows`. "
            "EXACT HEADER COUNT RULE: Count EVERY visible column header from left to right and NEVER omit columns such as '小型号', '网络型号', '地区/行货', '网络/WIFI版', or '备注'! For example, Huawei/Honor tablet sheets have 10 columns ('Mate pad Air系列 | 小型号 | 容量 | 充新 | 靓机 | 小花 | 大花 | 外爆 | 内爆可测 | 备注') and iPad sheets have 12 columns ('型号 | 网络型号 | 地区 | 版本 | 容量 | 充新 | 靓机 | 小花 | 大花 | 外爆 | 内爆可测 | 备注'). Every row MUST align 1-to-1 with every header column! "
            "IMPORTANT: When left-hand columns are nested vertically merged cells (for example a tablet series spanning 8 rows containing multiple '小型号' blocks like 'AST-W00 WIFI' / 'AST-W10 WIFI 柔光版', or an iPad model spanning 12 rows containing '行货' and 'WIFI版' / 'WIFI版 纳米' / '插卡版'), REPEAT EVERY left-hand column value onto EVERY row belonging to its horizontal grid box! "
            "NEVER COPY SECTION HEADER AS MODEL NAME: Repeated green sub-header labels ending in '系列' (such as 'Mate pad系列', 'Mate pad Air系列', 'Pro系列', 'Air系列') are COLUMN HEADERS ONLY—NEVER put them into a data row's model column! When a tall vertically merged model cell starts right below a green 'xxx系列' header and its centered model name (e.g. 'Mate pad Pro 2024年 12.2寸' or 'Mate pad Pro 23年 13.2寸') is printed lower down in the middle of the box, ALWAYS read downward inside that same merged box and fill that real model name onto the top rows too! "
            "NEVER pull a model name UPWARD from a lower horizontal border box onto orphan rows above the border line! If a row at the very top edge has a blank model cell separated by a dark horizontal border from the model below, leave its model cell empty '' or omit the clipped orphan row rather than assigning the NEXT model's name to it. "
            "SINGLE-LINE MODEL NAME RULE: NEVER put newline characters or literal '\\n' inside model names, network codes, capacities, or prices! If a model name wraps across two lines inside a narrow cell (for example '15 PRO' over 'MAX', or '荣耀Robot' over 'Phone', or 'MATE XT2' over '非凡大师'), ALWAYS join them on ONE line with a single space ('15 PRO MAX', '荣耀Robot Phone', 'MATE XT2 非凡大师'). "
            "BRAND & WATERMARK PRECISION: Carefully distinguish English letters vs digits in brand names and network codes (e.g. 'iQOO' with letters OO NOT digits 00, 'OPPO', 'ROG', 'A3108', 'A2639', 'PMK110', 'LAP-AL10'). Completely IGNORE faint diagonal watermarks (such as '靓机汇报价单') overlapping numbers or capacities—never attach stray watermark letters to capacities or prices. "
            "STRICT ANTI-HALLUCINATION RULE FOR CAPACITY & MODEL: Read EVERY SINGLE DIGIT of the capacity column directly from the image pixels (e.g. strictly distinguish '16+512' vs '16+1TB' and '8+256' vs '8+128'—NEVER auto-complete or extrapolate capacities from neighboring rows!). Read EVERY model name strictly from its own row/box—NEVER carry over the previous model name onto a row whose network code ('网络型号'/'具体型号') changed (e.g. do NOT copy 'A70S' onto 'C5560', or '14 PRO MAX' onto 'A2892'). If a cell is genuinely unreadable, output empty string '' instead of guessing! "
            "Do NOT include repeated sub-table header rows (like '型号 | 容量 | 靓机...') inside `rows`; instead represent each sub-block as an item in `sheets` with its exact `headers`. "
            "If a sub-table has '-' or blank placeholder columns (for example no highest price tier), KEEP that column slot so all blocks preserve consistent column structure and alignment. "
            + right_col_rule
            + "Preserve EVERY Chinese character verbatim with 100% precision (e.g. '高保充新-全套', '不看保修', '不需保修', '在保', '充新', '靓机', '小靓机', '小花', '大花', '外爆', '内爆可测', '网络型号', '蓝光/彩点', '扣费说明', '扣费项', '备注扣费项', '电芯'). NEVER substitute lookalike Chinese characters and NEVER translate titles or section names into English. "
            "Set `title` to the main quotation or document header visible in the image (e.g. '苹果行货 过保机 2026-11-25上午 回收报价'). "
            "Put top/edge vendor rules, validity notices, disclaimers, AND any top condition-definition legend table (like '成色说明 / 等级说明 / 外屏 / 边框背板 / 内屏显示') into `document_notes`—NEVER extract that condition-definition legend table as a sheet in `sheets` when phone/tablet price rows exist! "
            "Preserve exact text, model names, capacities (both Apple '128'/'256'/'512'/'1T' and Android '12+256'/'16+512'/'16+1TB'), numbers, currency formats (e.g. '¥1,284,000.00'), range strings (e.g. '3200-3300'), '+50', and leading zeros (e.g. '001042'). "
            "Every row in `rows` MUST have the exact same number of elements as `headers` (use empty string '' for blank cells). "
            "Set `col_types` accurately for each column (`text`, `number`, `currency`, `percent`, `date`)."
        )
    if custom_hint.strip():
        instruction += f"\nAdditional user hint: {custom_hint.strip()}"

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
        "Api-Revision": "2026-05-20",
    }

    target_resolution = "ultra_high"
    used_model = primary_model
    parsed: Optional[Dict[str, Any]] = None
    secondary_parsed: Optional[Dict[str, Any]] = None
    secondary_model_used = ""
    last_error = ""

    # 严格控制单次请求墙钟预算在 38 秒内（远低于 Vercel 60s 硬超时，彻底杜绝 Cloudflare 520 断连）
    wall_deadline = time.monotonic() + 38.0

    # 双模型并发交叉核验（3.5-flash-lite ∥ 3.1-flash-lite，并发耗时约等于单模型，几乎不增等待时间）
    if dual_verify and mode == "excel":
        m1 = candidate_models[0]
        m2 = next((c for c in candidate_models[1:] if c != m1), "gemini-3.1-flash-lite" if m1 != "gemini-3.1-flash-lite" else "gemini-3.5-flash-lite")
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_call_single_gemini_model, m1, instruction, b64_data, mime_type, headers, api_key, 22, target_resolution, wall_deadline)
            f2 = pool.submit(_call_single_gemini_model, m2, instruction, b64_data, mime_type, headers, api_key, 22, target_resolution, wall_deadline)
            p1, err1, fatal1 = f1.result()
            p2, err2, fatal2 = f2.result()

        if p1 is not None and p2 is not None:
            parsed = p1
            used_model = m1
            secondary_parsed = p2
            secondary_model_used = m2
        elif p1 is not None:
            parsed = p1
            used_model = m1
        elif p2 is not None:
            parsed = p2
            used_model = m2
        else:
            if fatal1:
                raise RuntimeError(err1)
            if fatal2:
                raise RuntimeError(err2)
            # 双方首次并发都偶发波动：仅在剩余安全预算 >= 9 秒时做 1 次快速兜底重试，且避开刚才 429/503/超时的主模型优先切备用模型
            rem_sec = wall_deadline - time.monotonic()
            if rem_sec >= 9.0:
                m1_throttled = any(k in (err1 or "") for k in ("429", "503", "超时", "Timeout"))
                fallback_m = m2 if m1_throttled else m1
                p3, err3, fatal3 = _call_single_gemini_model(
                    fallback_m, instruction, b64_data, mime_type, headers, api_key, min(14, int(rem_sec)), target_resolution, wall_deadline
                )
                if p3 is not None:
                    parsed = p3
                    used_model = fallback_m
                else:
                    last_error = err3 or err1 or err2
                    if fatal3:
                        raise RuntimeError(last_error)
            else:
                last_error = err1 or err2
    else:
        single_budget = [18, 12, 8] if model_tier != "accurate" else [20, 12, 8]
        for attempt_idx, candidate in enumerate(candidate_models):
            rem_sec = wall_deadline - time.monotonic()
            if rem_sec < 5.0:
                break
            if attempt_idx > 0:
                time.sleep(min(0.6 * attempt_idx, max(0.0, rem_sec - 5.0)))
            req_timeout = min(single_budget[min(attempt_idx, len(single_budget) - 1)], int(max(5.0, wall_deadline - time.monotonic())))
            cand_parsed, err_msg, is_fatal = _call_single_gemini_model(
                candidate, instruction, b64_data, mime_type, headers, api_key, req_timeout, target_resolution, wall_deadline
            )
            if cand_parsed is not None:
                parsed = cand_parsed
                used_model = candidate
                break
            last_error = err_msg
            if is_fatal:
                raise RuntimeError(last_error)

    if parsed is None:
        raise RuntimeError(last_error or "Gemini 视觉识别服务暂时繁忙，请稍后重试。")

    stem = Path(filename or "extracted").stem
    normalized = normalize_table_grid(parsed, fallback_name=stem)
    quote_title = _pick_quote_title(normalized["title"], stem)

    any_quote = any(
        _is_quotation_sheet(sh.get("headers") or [], sh.get("rows") or [], quote_title or sh.get("name", ""), norm_doc_type)
        for sh in normalized["sheets"]
    )
    if any_quote:
        raw_sheets = [sh for sh in normalized["sheets"] if not _is_condition_legend_sheet(sh)] or normalized["sheets"]
    else:
        raw_sheets = normalized["sheets"]

    # 若启用「仅提机型+价格列」，仅对报价单剥离右侧备注/扣费列，绝不误删通用财务/发票表的备注列
    if effective_prices_only and any_quote:
        for rsh in raw_sheets:
            sh_hdrs = rsh.get("headers") or []
            keep_cols = [i for i, h in enumerate(sh_hdrs) if not _is_note_header(h)]
            if keep_cols and len(keep_cols) < len(sh_hdrs):
                col_remap = {old_i: new_i for new_i, old_i in enumerate(keep_cols)}
                rsh["headers"] = [sh_hdrs[i] for i in keep_cols]
                old_types = rsh.get("col_types") or []
                rsh["col_types"] = [old_types[i] if i < len(old_types) else "text" for i in keep_cols]
                rsh["rows"] = [[r[i] if i < len(r) else "" for i in keep_cols] for r in (rsh.get("rows") or [])]
                rsh["warnings"] = [
                    {"row": w["row"], "col": col_remap[int(w["col"])], "reason": w.get("reason", "")}
                    for w in (rsh.get("warnings") or [])
                    if int(w.get("col", -1)) in col_remap
                ]

    for rsh in raw_sheets:
        rsh_is_quote = _is_quotation_sheet(
            rsh.get("headers") or [], rsh.get("rows") or [], quote_title or rsh.get("name", ""), norm_doc_type
        )
        r_rows, r_warns = _clean_quotation_rows(
            rsh["headers"], rsh["rows"], rsh.get("warnings") or [], is_quotation=rsh_is_quote
        )
        rsh["rows"] = r_rows
        rsh["warnings"] = r_warns
        rsh.setdefault("section_header_rows", [])
        rsh["style_preset"] = "quotation" if rsh_is_quote else "general"

    # 若双模型均返回有效结果：先对副模型同步做规范化+合并继承清洗，再逐格交叉核验（分歧留空标黄！）
    dual_verified = False
    dual_conflicts = 0
    if secondary_parsed is not None:
        sec_norm = normalize_table_grid(secondary_parsed, fallback_name=stem)
        sec_raw = (
            [sh for sh in sec_norm["sheets"] if not _is_condition_legend_sheet(sh)] or sec_norm["sheets"]
            if any_quote
            else sec_norm["sheets"]
        )
        for ssh in sec_raw:
            ssh_is_quote = _is_quotation_sheet(
                ssh.get("headers") or [], ssh.get("rows") or [], quote_title or ssh.get("name", ""), norm_doc_type
            )
            s_rows, s_warns = _clean_quotation_rows(
                ssh["headers"], ssh["rows"], ssh.get("warnings") or [], is_quotation=ssh_is_quote
            )
            ssh["rows"] = s_rows
            ssh["warnings"] = s_warns
        dual_conflicts = _cross_verify_dual_sheets(raw_sheets, sec_raw, used_model, secondary_model_used)
        dual_verified = True

    merged_sheet = merge_sheets_into_single_quotation(raw_sheets, quote_title, stem, doc_type=norm_doc_type)
    suggest_split = _should_keep_multi_sheets_separate(raw_sheets, doc_type=norm_doc_type)
    detected_doc_type = "quotation" if any_quote else "general"

    normalized["quote_title"] = quote_title
    normalized["raw_sheets"] = raw_sheets
    normalized["merged_sheet"] = merged_sheet
    normalized["sheets"] = [merged_sheet] if (merge_per_image and not suggest_split) else raw_sheets
    normalized["engine"] = f"{used_model} × {secondary_model_used}" if dual_verified else used_model
    normalized["is_demo"] = False
    normalized["detected_doc_type"] = detected_doc_type
    normalized["suggest_split_sheets"] = suggest_split
    mode_tag = "报价单模式" if any_quote else "通用表格模式"
    if dual_verified:
        if dual_conflicts > 0:
            normalized["notice"] = f"双模型交叉核验完成（{mode_tag} · 检出 {dual_conflicts} 处分歧已留空标黄）"
        else:
            normalized["notice"] = f"双模型交叉核验 100% 一致通过（{mode_tag}）"
    elif used_model != primary_model:
        normalized["notice"] = f"主模型高峰限流，已自动切换 {used_model} 完成识别（{mode_tag}）"
    else:
        normalized["notice"] = f"已通过 {used_model} 多模态引擎完成识别（{mode_tag}）"
    return normalized

import base64
import json
import re
import time
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
        headers = [str(h if h is not None else "").strip() for h in (sheet.get("headers") or [])]
        raw_rows = sheet.get("rows") or []
        str_rows: List[List[str]] = []
        max_cols = len(headers)
        for row in raw_rows:
            if isinstance(row, list):
                cells = [str(c if c is not None else "").strip() for c in row]
            else:
                cells = [str(row if row is not None else "").strip()]
            max_cols = max(max_cols, len(cells))
            str_rows.append(cells)

        if max_cols == 0:
            max_cols = 1
        while len(headers) < max_cols:
            headers.append(f"列{len(headers) + 1}")
        headers = [h or f"列{i + 1}" for i, h in enumerate(headers)]

        for row in str_rows:
            while len(row) < max_cols:
                row.append("")

        raw_types = [str(t).lower() for t in (sheet.get("col_types") or [])]
        col_types: List[str] = []
        for col_i in range(max_cols):
            t = raw_types[col_i] if col_i < len(raw_types) else ""
            if t not in VALID_COL_TYPES:
                col_vals = [r[col_i] for r in str_rows]
                t = _infer_col_type(col_vals)
            col_types.append(t)

        # 校验合并范围，确保不越界
        valid_merges: List[Dict[str, int]] = []
        row_count = len(str_rows)
        for m in (sheet.get("merges") or []):
            try:
                sr = int(m["start_row"])
                sc = int(m["start_col"])
                er = int(m["end_row"])
                ec = int(m["end_col"])
            except Exception:
                continue
            if 0 <= sr <= er < row_count and 0 <= sc <= ec < max_cols and (sr != er or sc != ec):
                valid_merges.append({"start_row": sr, "start_col": sc, "end_row": er, "end_col": ec})

        # 合并模型提示的 warnings 与本地规则巡检
        warnings_map: Dict[tuple, str] = {}
        for w in (sheet.get("warnings") or []):
            try:
                wr = int(w["row"])
                wc = int(w["col"])
                if 0 <= wr < row_count and 0 <= wc < max_cols:
                    warnings_map[(wr, wc)] = str(w.get("reason") or "模型提示需人工核对")
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


_CAP_VALUE_RE = re.compile(r"^(?:32|64|128|256|512|1024)(?:g|gb)?$|^(?:1|2)t(?:b)?$", re.IGNORECASE)


def _is_note_header(h: str) -> bool:
    hn = _norm_header(h)
    return any(k in hn for k in ("备注", "蓝光", "彩点", "面容", "拆修", "扣费", "说明", "规则", "note", "remark"))


def _detect_column_roles(headers: List[str]) -> Dict[str, Any]:
    """
    动态识别报价单各列语义角色（彻底告别硬编码 row[1] 为容量）：
    兼容 `型号 | 容量 | 价格...` 与 `型号 | 网络型号 | 容量 | 价格... | 扣费说明...` 等任意列布局。
    """
    sec_col: Optional[int] = None
    model_col: Optional[int] = None
    code_col: Optional[int] = None
    cap_col: Optional[int] = None
    note_cols: List[int] = []

    normed = [_norm_header(h) for h in (headers or [])]

    # 先精确匹配主型号列（避免被「网络型号」抢占）
    for i, hn in enumerate(normed):
        if hn == "分类区块":
            sec_col = i
        elif hn in {"型号", "机型", "model", "设备名称", "手机型号"} and model_col is None:
            model_col = i

    for i, hn in enumerate(normed):
        if i in (sec_col, model_col):
            continue
        if code_col is None and any(k in hn for k in ("网络型号", "型号编码", "机器编码", "代号", "sku", "编码")):
            code_col = i
        elif model_col is None and any(k in hn for k in ("型号", "机型", "model", "设备")):
            model_col = i
        elif cap_col is None and any(k in hn for k in ("容量", "内存", "配置", "规格", "storage", "gb")):
            cap_col = i
        elif _is_note_header(headers[i]):
            note_cols.append(i)

    excluded = {c for c in (sec_col, model_col, code_col, cap_col) if c is not None} | set(note_cols)
    price_cols = [i for i in range(len(normed)) if i not in excluded]
    return {
        "sec_col": sec_col,
        "model_col": model_col,
        "code_col": code_col,
        "cap_col": cap_col,
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
    s = _norm_cap_str(val)
    if not s:
        return None
    m_t = re.match(r"^(\d+(?:\.\d+)?)t$", s)
    if m_t:
        return int(round(float(m_t.group(1)) * 1024))
    m_g = re.match(r"^(\d+)g?$", s)
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
    note_cols = set(roles.get("note_cols") or []) if roles else set()

    if model_col is None and r:
        model_col = 0
    if cap_col is None:
        # 动态兜底：在前 4 列中自动寻找形如 128/256G/512/1T 的容量单元格
        for ci in range(1, min(4, len(r))):
            if ci == code_col:
                continue
            cell_s = re.sub(r"\s+", "", str(r[ci] or ""))
            if _CAP_VALUE_RE.match(cell_s):
                cap_col = ci
                break
        if cap_col is None and len(r) > 1:
            cap_col = 1

    m_raw = str(r[model_col] if model_col is not None and model_col < len(r) else "").strip()
    c_raw = str(r[cap_col] if cap_col is not None and cap_col < len(r) else "").strip()
    m_norm = re.sub(r"[\s\-_/]+", "", m_raw).lower()
    c_norm = _norm_cap_str(c_raw)

    skip_set = {c for c in (model_col, cap_col, code_col, roles.get("sec_col") if roles else None) if c is not None} | note_cols
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


def merge_sheets_into_single_quotation(
    sheets: List[Dict[str, Any]],
    quote_title: str,
    fallback_stem: str = "报价单",
) -> Dict[str, Any]:
    """
    忠实原图合表：
    - 彻底去掉人工「分类/区块」列，A 列直接为原图第 1 列（如 型号）；
    - 当后续区块表头文字与上一区块不同时，在数据流中保留该区块原表头行并记入 section_header_rows，
      导出 Excel 时自动渲染为苹果绿区块表头，最大程度贴近原图与 WPS 效果。
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
        }

    # 先对每个区块独立清洗（防重复表头/残缺行/合并空型号），避免干扰后续区块小表头
    cleaned_sheets: List[Dict[str, Any]] = []
    for sh in sheets:
        sh_copy = json.loads(json.dumps(sh))
        c_rows, c_warns = _clean_quotation_rows(
            sh_copy.get("headers") or [], sh_copy.get("rows") or [], sh_copy.get("warnings") or []
        )
        if c_rows:
            sh_copy["rows"] = c_rows
            sh_copy["warnings"] = c_warns
            cleaned_sheets.append(sh_copy)

    if not cleaned_sheets:
        cleaned_sheets = [json.loads(json.dumps(sheets[0]))]

    if len(cleaned_sheets) == 1:
        single = cleaned_sheets[0]
        single["name"] = final_name
        single.setdefault("section_header_rows", [])
        return _global_post_clean_and_audit(single)

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

        # 若当前区块表头与上一区块不同，则原样保留一条区块小表头行
        if s_idx > 0 and cur_header_row != prev_header_row and any(cur_header_row):
            section_header_rows.append(len(merged_rows))
            merged_rows.append(cur_header_row)
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

    result_sheet = {
        "name": final_name,
        "headers": unified_headers,
        "col_types": unified_types,
        "rows": merged_rows,
        "merges": [],
        "warnings": merged_warnings,
        "section_header_rows": section_header_rows,
    }
    return _global_post_clean_and_audit(result_sheet)


def _global_post_clean_and_audit(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """
    合表后全局二次精洗与异常逻辑自检（核心攻克超长图 13 混入 13 PRO 尾两行等跨块幽灵行）：
    1. 严格保留 section_header_rows 不动，并在每个连续数据段内做 6 行回看窗口去重：
       - 若本行与前 1~6 行中某行「容量一致 + 价格序列（>=2 列）完全一致」，且满足以下任一条件：
         a) 机型名兼容（如 13 PRO vs 13、17pro vs 17 或同机型），或
         b) 属于「容量回跳幽灵行」（当前行刚切入新机型且容量偏大如 512/1T，后续同机型行又从更小容量 128/256 重新起排），
         则判定为切缝幽灵行并剔除，保留前序完整机型行与编码，同步平移 section_header_rows 和 warnings 索引。
    2. 全局自检标黄（写入 warnings 供前端高亮复核）：
       - 同区块同机型出现重复容量；
       - 有效机型+容量行全价位缺失；
       - 同行档位价格严重违背该区块主导的从左到右递减规律（跳升超 15%）。
    """
    headers: List[str] = sheet.get("headers") or []
    rows: List[List[str]] = sheet.get("rows") or []
    warnings: List[Dict[str, Any]] = list(sheet.get("warnings") or [])
    sec_headers_set = set(int(x) for x in (sheet.get("section_header_rows") or []))

    if not headers or not rows:
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
            for prev_i in range(len(new_rows) - 1, lookback_start - 1, -1):
                m_prev, c_prev, p_prev = new_sems[prev_i]
                same_cap = (cap_col is None) or (bool(c_cur) and c_cur == c_prev)
                if same_cap and p_prev == p_cur:
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

    # 映射原有 warnings
    new_warnings: List[Dict[str, Any]] = []
    warn_keys: set = set()
    for w in warnings:
        try:
            orig_r = int(w["row"])
            col_c = int(w["col"])
            if orig_r in idx_map:
                nr = idx_map[orig_r]
                if (nr, col_c) not in warn_keys:
                    warn_keys.add((nr, col_c))
                    new_warnings.append({"row": nr, "col": col_c, "reason": str(w.get("reason") or "")})
        except Exception:
            continue

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

        # 2. 逐行检查：同机型重复容量 / 全价位空缺 / 价格档位严重倒挂
        seen_model_cap: Dict[Tuple[str, str], int] = {}
        for r_idx in seg:
            m_norm, c_norm, _ = new_sems[r_idx]
            nums = row_nums[r_idx]
            if m_norm and c_norm:
                key = (m_norm, c_norm)
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
) -> tuple[List[List[str]], List[Dict[str, Any]]]:
    """
    Q2 智能清洗报价单异常行：
    1. 剔除因分段识别混入数据区的重复表头行（如 型号/容量/靓机 行）；
    2. 剔除切片边界处「缺型号但容量+价格与下一行重合」的残缺重复行；
    3. 区块内合并单元格造成的空型号行向下继承机型名；仍无归属机型的孤立行自动过滤，彻底杜绝无机型却有价格。
    """
    if not rows or not headers:
        return rows, warnings

    roles = _detect_column_roles(headers)
    model_col: Optional[int] = roles["model_col"]
    cap_col: Optional[int] = roles["cap_col"]
    sec_col: Optional[int] = roles["sec_col"]
    code_col: Optional[int] = roles["code_col"]
    note_cols_set = set(roles["note_cols"])

    if model_col is None:
        return rows, warnings

    header_tokens = {"型号", "机型", "model", "容量", "内存", "备注", "扣费项", "蓝光", "蓝光/彩点", "面容/拆修"}

    def _is_embedded_header(r: List[str]) -> bool:
        m_val = (r[model_col] if model_col < len(r) else "").strip().lower()
        c_val = (r[cap_col] if cap_col is not None and cap_col < len(r) else "").strip().lower()
        if m_val in {"型号", "机型", "model"}:
            return True
        if c_val in {"容量", "内存"}:
            hits = sum(1 for cell in r if cell.strip().lower() in header_tokens or "靓机" in cell or "小花" in cell)
            if hits >= 2:
                return True
        return False

    def _price_sig(r: List[str]) -> tuple:
        vals = []
        for idx, val in enumerate(r):
            if idx in (model_col, cap_col, sec_col, code_col) or idx in note_cols_set:
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

    # 第二遍：去除切片交界处「缺型号但容量+价格与下一行完全相同」的残缺行 + 向下补全合并型号
    step2_rows: List[List[str]] = []
    step2_map: Dict[int, int] = {}
    last_section = None
    last_model = ""

    for idx, r in enumerate(step1_rows):
        cur_sec = r[sec_col].strip() if sec_col is not None and sec_col < len(r) else ""
        if cur_sec != last_section:
            last_section = cur_sec
            last_model = ""

        m_val = r[model_col].strip() if model_col < len(r) else ""
        c_val = r[cap_col].strip() if cap_col is not None and cap_col < len(r) else ""

        if not m_val:
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

            # 若前面同一区块已有合法机型，且本行有容量或价格 -> 继承合并单元格的机型名
            if last_model and (c_val or p_sig):
                r[model_col] = last_model
            else:
                # 无归属机型的孤立切片残行直接丢弃
                continue
        else:
            last_model = m_val

        step2_map[idx] = len(step2_rows)
        step2_rows.append(r)

    # 第三遍：智能消除相邻 1~6 行内的「同容量 + 同价格序列 + 截断/兼容机型名」重复行
    # 典型案例：切片缝把 17pro 截断成 17，或把 13 PRO 的 512/1T 错标成 13 / A2634
    cleaned_rows: List[List[str]] = []
    step3_map: Dict[int, int] = {}
    for idx, r in enumerate(step2_rows):
        m_cur = r[model_col].strip() if model_col < len(r) else ""
        c_cur = _norm_cap_str(r[cap_col]) if cap_col is not None and cap_col < len(r) else ""
        p_cur = _price_sig(r)

        merged_into: Optional[int] = None
        if p_cur:
            search_start = max(0, len(cleaned_rows) - 6)
            for prev_i in range(len(cleaned_rows) - 1, search_start - 1, -1):
                pr = cleaned_rows[prev_i]
                m_prev = pr[model_col].strip() if model_col < len(pr) else ""
                c_prev = _norm_cap_str(pr[cap_col]) if cap_col is not None and cap_col < len(pr) else ""
                p_prev = _price_sig(pr)
                same_cap = (cap_col is None) or (bool(c_cur) and c_cur == c_prev)
                if same_cap and p_prev == p_cur and _models_compatible(m_prev, m_cur):
                    cleaned_rows[prev_i] = _merge_dup_pair(pr, r)
                    merged_into = prev_i
                    break

        if merged_into is not None:
            step3_map[idx] = merged_into
        else:
            step3_map[idx] = len(cleaned_rows)
            cleaned_rows.append(r)

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
) -> bool:
    """
    动态列感知判断两段切片重叠处的两行是否为同一条数据：
    - 兼容 `型号 | 容量 | 价格...` 与 `型号 | 网络型号 | 容量 | 价格...` 等多前置列结构；
    - 兼容切缝导致的短机型截断（17 vs 17pro）或错标邻近机型（13 PRO 尾行被错套 13 / A2634）。
    """
    if _row_signature(prev_row) == _row_signature(next_row):
        return True
    m_a, cap_a, p_prices = _extract_row_semantics(prev_row, prev_roles)
    m_b, cap_b, n_prices = _extract_row_semantics(next_row, next_roles)

    if m_a and m_a == m_b and cap_a and cap_a == cap_b:
        if not p_prices or not n_prices or p_prices == n_prices:
            return True

    # 切缝处容量一致 + 价格序列（>=2 列）完全一致：
    # 即使切片顶部漏掉合并单元格型号（导致错套成 13 / A2634 或截断成 17），也能精准识别为重复行
    if cap_a and cap_a == cap_b and len(p_prices) >= 2 and p_prices == n_prices:
        if _models_compatible_norm(m_a, m_b):
            return True
        # 切缝边界强信号：>=3 个价位数字一字不差且容量相同，几乎必然是同一行重叠
        if len(p_prices) >= 3:
            return True
    elif cap_a and cap_a == cap_b and p_prices and p_prices == n_prices and _models_compatible_norm(m_a, m_b):
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
) -> int:
    """
    计算前后两段切片在重叠区域的重复行数（动态适配任意前置列布局）：
    - 若匹配到切缝残缺行或跨机型幽灵行，自动保留更完整的一方并跳过 next_rows 起始重复行。
    """
    if not prev_rows or not next_rows:
        return 0
    prev_roles = _detect_column_roles(prev_headers) if prev_headers else None
    next_roles = _detect_column_roles(next_headers) if next_headers else prev_roles

    prev_tail_start = max(0, len(prev_rows) - max_window)
    prev_tail = prev_rows[prev_tail_start:]
    next_head = next_rows[:max_window]
    max_k = min(len(prev_tail), len(next_head))
    for k in range(max_k, 0, -1):
        if all(_rows_overlap_equivalent(prev_tail[-k + i], next_head[i], prev_roles, next_roles) for i in range(k)):
            for i in range(k):
                target_idx = len(prev_rows) - k + i
                prev_rows[target_idx] = _merge_overlap_row(prev_rows[target_idx], next_head[i], prev_roles, next_roles)
            return k

    skip = 0
    for idx in range(min(6, len(next_head))):
        matched_prev: Optional[int] = None
        for p_offset in range(len(prev_tail) - 1, -1, -1):
            if _rows_overlap_equivalent(prev_tail[p_offset], next_head[idx], prev_roles, next_roles):
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
) -> Dict[str, Any]:
    """将同一张超长报价单切分后的多段识别结果无损拼接、重叠去重，并合成为 1 份完整报价单。"""
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
            if same_name or (same_headers and is_continuation):
                prev_rows = prev.get("rows") or []
                next_rows = sh_copy.get("rows") or []
                skip = _dedupe_boundary_overlap(
                    prev_rows, next_rows, prev.get("headers"), sh_copy.get("headers")
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
            else:
                # 不同区块名或列数微差时，仍在相邻切片边界做动态列感知去重
                skip = _dedupe_boundary_overlap(
                    prev.get("rows") or [], sh_copy.get("rows") or [], prev.get("headers"), sh_copy.get("headers")
                )
                sh_copy["rows"] = (sh_copy.get("rows") or [])[skip:]
            if sh_copy.get("rows"):
                combined_raw_sheets.append(sh_copy)

    for rsh in combined_raw_sheets:
        r_rows, r_warns = _clean_quotation_rows(
            rsh["headers"], rsh["rows"], rsh.get("warnings") or []
        )
        rsh["rows"] = r_rows
        rsh["warnings"] = r_warns
        rsh.setdefault("section_header_rows", [])

    merged_sheet = merge_sheets_into_single_quotation(combined_raw_sheets, quote_title, stem)
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
        "sheets": [merged_sheet] if merge_per_image else combined_raw_sheets,
        "plain_text": "\n".join(plain_lines),
        "engine": last_res.get("engine", DEFAULT_GEMINI_MODEL),
        "is_demo": bool(last_res.get("is_demo", False)),
        "notice": f"长图已分 {len(slice_results)} 段高清切片提取并去重合并（共 {len(merged_sheet.get('rows') or [])} 行）",
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
) -> Dict[str, Any]:
    """
    核心图像表格提取函数（高可靠加固版）：
    - 默认锁定极速稳定模型 gemini-3.5-flash-lite / gemini-3.1-flash-lite（绝不擅自切换易 503 的 Hydrogen）；
    - 超时预算严格控制在 Vercel 60s 函数上限内（22s + 16s + 12s <= 52s）；
    - 除 429/5xx/超时自动切换外，对空输出、畸形 JSON 或 0 行结果同样触发自动跨模型兜底重试。
    """
    api_key = _get_gemini_api_key()
    if force_demo or not api_key:
        demo_res = _build_demo_result(filename, mode)
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

    right_col_rule = (
        "PRICES-ONLY MODE ENABLED: Extract ONLY the left identification columns (e.g. '型号', '网络型号', '容量/内存') and all numeric price tier columns (e.g. '靓机', '小花', '大花', '外爆', '内爆可测'). COMPLETELY SKIP long rightmost deduction/remark text columns ('备注', '扣费说明', '扣费项', '蓝光/彩点') to maximize speed and numeric precision. "
        if prices_only
        else "NEVER omit rightmost remark or deduction columns! Even if the rightmost columns contain dense multi-line Chinese rules (like '绿色-50 / 港澳版-150 / 像头水印-100 / 弹屏-450') or share a merged header ('扣费说明' / '备注'), ALWAYS include them faithfully as dedicated rightmost column(s). "
    )

    instruction = (
        "You are an expert document and quotation-sheet OCR extraction engine. "
        "CRITICAL: Extract EVERY SINGLE ROW and EVERY table/block visible in this image from the very top to the very bottom. "
        "NEVER omit rows, NEVER omit price cells, NEVER stop after the first section, and NEVER truncate the output. "
        "VISUAL HEADER ANCHOR RULE: If there is a bright RED horizontal divider bar near the top of the image, the strip ABOVE the red bar is a copied visual column-header reference from the top of the sheet—use it ONLY to keep exact column names and left-to-right column alignment, and put ONLY the data rows BELOW the red bar into `rows`. "
        "IMPORTANT: When a model name cell (or model code cell like '13 PRO' / 'A2639') spans multiple capacity rows (vertically merged cell over 128/256/512/1T), REPEAT the exact model name and code on EVERY row belonging to that horizontal grid box! "
        "NEVER pull a model name UPWARD from a lower horizontal border box onto orphan rows above the border line! If a row at the very top edge has a blank model cell separated by a dark horizontal border from the model below, leave its model cell empty '' or omit the clipped orphan row rather than assigning the NEXT model's name to it. "
        "Do NOT include repeated sub-table header rows (like '型号 | 容量 | 靓机...') inside `rows`; instead represent each sub-block as an item in `sheets` with its exact `headers`. "
        "If a sub-table has '-' or blank placeholder columns (for example no highest price tier), KEEP that column slot so all blocks preserve consistent column structure and alignment. "
        + right_col_rule
        + "Preserve EVERY Chinese character verbatim with 100% precision (e.g. '高保充新-全套', '不看保修', '不需保修', '在保', '靓机', '小靓机', '小花', '大花', '外爆', '内爆可测', '网络型号', '蓝光/彩点', '扣费说明', '扣费项', '电芯'). NEVER substitute lookalike Chinese characters and NEVER translate titles or section names into English. "
        "Set `title` to the main quotation header visible in the image (e.g. vendor name + date like '苹果行货 过保机 2026-11-25上午 回收报价'). "
        "Put top/edge vendor rules, validity notices, or disclaimers outside data rows into `document_notes` (do NOT mix them into table rows). "
        "Preserve exact text, model names, capacities, numbers, range strings (e.g. '3200-3300'), '+50', and leading zeros (e.g. '001042'). "
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

    target_resolution = "ultra_high" if model_tier == "accurate" else "high"
    used_model = primary_model
    parsed: Optional[Dict[str, Any]] = None
    last_error = ""

    for attempt_idx, candidate in enumerate(candidate_models):
        if attempt_idx > 0:
            time.sleep(0.5 * attempt_idx)
        req_timeout = timeout_budget[min(attempt_idx, len(timeout_budget) - 1)]
        image_block: Dict[str, Any] = {
            "type": "image",
            "data": b64_data,
            "mime_type": mime_type,
            "resolution": target_resolution,
        }
        payload = {
            "model": candidate,
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
        try:
            resp = requests.post(
                GEMINI_INTERACTIONS_URL,
                json=payload,
                headers=headers,
                timeout=req_timeout,
            )
            # 若个别端点暂不支持 resolution 字段则返回 400，立即剥离后原模型重试
            if resp.status_code == 400 and "resolution" in resp.text.lower():
                image_block.pop("resolution", None)
                resp = requests.post(
                    GEMINI_INTERACTIONS_URL,
                    json=payload,
                    headers=headers,
                    timeout=req_timeout,
                )
        except requests.RequestException as exc:
            last_error = f"连接 Gemini 视觉识别服务超时或网络异常：{type(exc).__name__}"
            continue

        if resp.status_code in (401, 403):
            raise RuntimeError("GEMINI_API_KEY 鉴权失败，请检查 Vercel 或 .env 中的密钥配置是否正确。")
        if resp.status_code in (429, 500, 502, 503, 504):
            err_preview = resp.text[:240].replace(api_key, "***")
            last_error = f"Gemini 模型 {candidate} 暂不可用 (HTTP {resp.status_code})：{err_preview}"
            continue
        if resp.status_code != 200:
            err_preview = resp.text[:240].replace(api_key, "***")
            raise RuntimeError(f"Gemini 视觉识别接口返回异常 (HTTP {resp.status_code})：{err_preview}")

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
                last_error = f"Gemini 模型 {candidate} 返回空表格，正在切换模型重试"
                continue
            parsed = candidate_parsed
            used_model = candidate
            break
        except Exception as exc:
            last_error = f"Gemini 模型 {candidate} 返回结构解析异常（{type(exc).__name__}），正在自动重试"
            continue

    if parsed is None:
        raise RuntimeError(last_error or "Gemini 视觉识别服务暂时繁忙，请稍后重试。")

    stem = Path(filename or "extracted").stem
    normalized = normalize_table_grid(parsed, fallback_name=stem)
    raw_sheets = normalized["sheets"]
    for rsh in raw_sheets:
        r_rows, r_warns = _clean_quotation_rows(
            rsh["headers"], rsh["rows"], rsh.get("warnings") or []
        )
        rsh["rows"] = r_rows
        rsh["warnings"] = r_warns
        rsh.setdefault("section_header_rows", [])

    quote_title = _pick_quote_title(normalized["title"], stem)
    merged_sheet = merge_sheets_into_single_quotation(raw_sheets, quote_title, stem)

    normalized["quote_title"] = quote_title
    normalized["raw_sheets"] = raw_sheets
    normalized["merged_sheet"] = merged_sheet
    normalized["sheets"] = [merged_sheet] if merge_per_image else raw_sheets
    normalized["engine"] = used_model
    normalized["is_demo"] = False
    if used_model != primary_model:
        normalized["notice"] = f"主模型高峰限流，已自动切换 {used_model} 完成结构化表格识别"
    else:
        normalized["notice"] = f"已通过 {used_model} 多模态引擎完成结构化表格识别"
    return normalized

import base64
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _map_subsheet_columns(
    sh_headers: List[str],
    sh_types: List[str],
    unified_headers: List[str],
    unified_types: List[str],
    header_to_col: Dict[str, int],
    master_len: int,
) -> List[int]:
    """
    智能对齐同一报价单内各子表的列映射（杜绝因表头文字微调把价格挤到右侧 K/L/M 列）：
    1. 列数与主表相同时，直接按位置 1:1 对齐（型号/容量/各价位/面容/备注/电池项全部留在原位）；
    2. 列数少于主表时（如某区块省略了 '-' 占位列），先匹配已知锚点列名，剩余价位列自动填入主表空余价格槽位（右对齐优先）。
    """
    n = len(sh_headers)
    if n == master_len:
        return [1 + i for i in range(n)]

    norm_master = {_norm_header(h): idx for idx, h in enumerate(unified_headers) if idx >= 1}
    assigned: List[Optional[int]] = [None] * n
    used_targets: set = set()

    # 第一步：匹配锚点列（精确或归一化匹配）
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

    # 第二步：为未匹配的价位列分配主表空余槽位（保持相对顺序，缺高保充新位时靠右对齐）
    unmatched_indices = [i for i, v in enumerate(assigned) if v is None]
    free_slots = [c for c in range(1, 1 + master_len) if c not in used_targets]

    if unmatched_indices and len(free_slots) >= len(unmatched_indices):
        # 靠右对齐，使 靓机/小靓机/小花 落在对应的后几列价格槽位
        offset = len(free_slots) - len(unmatched_indices)
        for k, local_idx in enumerate(unmatched_indices):
            assigned[local_idx] = free_slots[offset + k]
            used_targets.add(free_slots[offset + k])
    else:
        for local_idx in unmatched_indices:
            if free_slots:
                slot = free_slots.pop(0)
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

    return [int(c if c is not None else 1) for c in assigned]


def merge_sheets_into_single_quotation(
    sheets: List[Dict[str, Any]],
    quote_title: str,
    fallback_stem: str = "报价单",
) -> Dict[str, Any]:
    """
    Q1 规格落地：将同一张报价单图片内的多个区块子表合并为单一 Sheet：
    - 1 个子表时直接保留原列结构；
    - 多个子表时首列增设「分类/区块」，并采用位置+锚点智能对齐主表头（保证价格始终留在同一批列中）。
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
        }

    if len(sheets) == 1:
        single = json.loads(json.dumps(sheets[0]))
        single["name"] = final_name
        return single

    # 选用列数最完整（同列数取最靠前）的子表作为主表头基准
    master_sheet = max(sheets, key=lambda s: len(s.get("headers") or []))
    master_headers = [
        str(h or f"列{i + 1}").strip() or f"列{i + 1}"
        for i, h in enumerate(master_sheet.get("headers") or [])
    ]
    master_types = master_sheet.get("col_types") or []
    master_len = len(master_headers)

    unified_headers: List[str] = ["分类/区块"] + master_headers
    unified_types: List[str] = ["text"] + [
        (master_types[i] if i < len(master_types) and master_types[i] in VALID_COL_TYPES else "text")
        for i in range(master_len)
    ]
    header_to_col: Dict[str, int] = {h: 1 + i for i, h in enumerate(master_headers)}

    # 预先计算每个子表的列映射（可能会在极端多列时扩展 unified_headers）
    mappings: List[List[int]] = []
    for sh in sheets:
        sh_headers = [str(h or f"列{i + 1}").strip() or f"列{i + 1}" for i, h in enumerate(sh.get("headers") or [])]
        sh_types = sh.get("col_types") or []
        mappings.append(
            _map_subsheet_columns(sh_headers, sh_types, unified_headers, unified_types, header_to_col, master_len)
        )

    total_cols = len(unified_headers)
    merged_rows: List[List[str]] = []
    merged_warnings: List[Dict[str, Any]] = []

    for s_idx, sh in enumerate(sheets):
        section_name = str(sh.get("name") or f"区块{s_idx + 1}").strip()
        local_to_global = mappings[s_idx]

        base_row_offset = len(merged_rows)
        for row in (sh.get("rows") or []):
            new_row = [""] * total_cols
            new_row[0] = section_name
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

    return {
        "name": final_name,
        "headers": unified_headers,
        "col_types": unified_types,
        "rows": merged_rows,
        "merges": [],
        "warnings": merged_warnings,
    }


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


def _dedupe_boundary_overlap(prev_rows: List[List[str]], next_rows: List[List[str]], max_window: int = 8) -> int:
    """计算前后两段切片在重叠区域的重复行数，返回 next_rows 应跳过的起始下标。"""
    if not prev_rows or not next_rows:
        return 0
    prev_tail = [_row_signature(r) for r in prev_rows[-max_window:]]
    next_head = [_row_signature(r) for r in next_rows[:max_window]]
    max_k = min(len(prev_tail), len(next_head))
    for k in range(max_k, 0, -1):
        if prev_tail[-k:] == next_head[:k]:
            return k
    tail_set = set(s for s in prev_tail if s.replace("|", ""))
    tail_keys = set(_row_key_signature(r) for r in prev_rows[-max_window:] if any(r[:2]))
    skip = 0
    for idx, sig in enumerate(next_head[:4]):
        key_sig = _row_key_signature(next_rows[idx])
        if sig in tail_set or (key_sig.replace("|", "") and key_sig in tail_keys):
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
                skip = _dedupe_boundary_overlap(prev_rows, next_rows)
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
                # 不同区块名或列数微差时，仍在相邻切片边界做 (型号+容量) 重叠去重
                skip = _dedupe_boundary_overlap(prev.get("rows") or [], sh_copy.get("rows") or [])
                sh_copy["rows"] = (sh_copy.get("rows") or [])[skip:]
            if sh_copy.get("rows"):
                combined_raw_sheets.append(sh_copy)

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


def extract_tables_from_image(
    image_bytes: bytes,
    filename: str,
    content_type: Optional[str] = None,
    mode: str = "excel",
    force_demo: bool = False,
    custom_hint: str = "",
    merge_per_image: bool = True,
    model_tier: str = "fast",
) -> Dict[str, Any]:
    """
    核心图像表格提取函数：
    - 默认采用极速高可靠模型 gemini-3.5-flash-lite（不输出重复 plain_text 节省 50% token，彻底告别 ReadTimeout 与半途截断）；
    - 支持 model_tier='accurate' 切换旗舰模型 gemini-4-hydrogen；
    - 遇 503/429/超时 自动切换备用模型。
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
        ordered = ["gemini-4-hydrogen", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    else:
        ordered = [env_model or DEFAULT_GEMINI_MODEL, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-4-hydrogen"]

    candidate_models: List[str] = []
    for m in ordered:
        if m and m not in candidate_models:
            candidate_models.append(m)
    candidate_models = candidate_models[:3]
    primary_model = candidate_models[0]

    instruction = (
        "You are an expert document and quotation-sheet OCR extraction engine. "
        "CRITICAL: Extract EVERY SINGLE ROW and EVERY table/block visible in this image from the very top to the very bottom. "
        "NEVER omit rows, NEVER omit price cells, NEVER stop after the first section, and NEVER truncate the output. "
        "If a sub-table has '-' or blank placeholder columns (for example no highest price tier), KEEP that column slot so all blocks preserve consistent column structure and alignment. "
        "Set `title` to the main quotation header visible in the image (e.g. vendor name + date like '新军强 靓机 11.24'). "
        "Put top/edge vendor rules, validity notices, or disclaimers outside data rows into `document_notes` (do NOT mix them into table rows). "
        "Preserve exact text, model names, capacities, numbers, currency symbols, range strings (e.g. '3200-3300'), '+50', and leading zeros (e.g. '001042'). "
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

    resp = None
    used_model = primary_model
    last_error = ""
    for attempt_idx, candidate in enumerate(candidate_models):
        payload = {
            "model": candidate,
            "store": False,
            "input": [
                {"type": "text", "text": instruction},
                {"type": "image", "data": b64_data, "mime_type": mime_type},
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
                timeout=36 if attempt_idx == 0 else 20,
            )
        except requests.RequestException as exc:
            last_error = f"连接 Gemini 视觉识别服务超时或网络异常：{type(exc).__name__}"
            resp = None
            continue

        if resp.status_code == 200:
            used_model = candidate
            break

        err_preview = resp.text[:240].replace(api_key, "***")
        if resp.status_code in (401, 403):
            raise RuntimeError("GEMINI_API_KEY 鉴权失败，请检查 Vercel 或 .env 中的密钥配置是否正确。")
        if resp.status_code in (429, 500, 502, 503, 504):
            last_error = (
                f"Gemini 模型 {candidate} 暂不可用 (HTTP {resp.status_code})：{err_preview}"
            )
            continue
        raise RuntimeError(f"Gemini 视觉识别接口返回异常 (HTTP {resp.status_code})：{err_preview}")

    if resp is None or resp.status_code != 200:
        raise RuntimeError(last_error or "Gemini 视觉识别服务暂时繁忙，请稍后重试。")

    raw_json = resp.json()
    text_out = _extract_text_from_interactions_response(raw_json).strip()
    if not text_out:
        raise RuntimeError("Gemini 模型未返回可解析的表格内容，请换一张更清晰的图片重试。")

    try:
        parsed = json.loads(text_out)
    except json.JSONDecodeError:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=re.IGNORECASE | re.DOTALL).strip()
        parsed = json.loads(cleaned)

    stem = Path(filename or "extracted").stem
    normalized = normalize_table_grid(parsed, fallback_name=stem)
    raw_sheets = normalized["sheets"]
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

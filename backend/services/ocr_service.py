import base64
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from database import _get_clean_env

GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_GEMINI_MODEL = "gemini-4-hydrogen"
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
        "plain_text": {
            "type": "string",
            "description": "Clean Markdown/plain-text representation of the table or text content.",
        },
        "sheets": {
            "type": "array",
            "description": "List of extracted tables, each represented as one worksheet.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Worksheet tab name (concise, under 28 characters).",
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
                        "description": "2D array of data rows (excluding the primary header row). Every row must match headers length.",
                    },
                    "merges": {
                        "type": "array",
                        "description": "Optional 0-indexed data-grid merge ranges (excluding header row).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start_row": {"type": "integer"},
                                "start_col": {"type": "integer"},
                                "end_row": {"type": "integer"},
                                "end_col": {"type": "integer"},
                            },
                            "required": ["start_row", "start_col", "end_row", "end_col"],
                        },
                    },
                    "warnings": {
                        "type": "array",
                        "description": "Cells that look blurry, ambiguous, or need human verification.",
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
    "required": ["title", "sheets", "plain_text"],
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


def merge_sheets_into_single_quotation(
    sheets: List[Dict[str, Any]],
    quote_title: str,
    fallback_stem: str = "报价单",
) -> Dict[str, Any]:
    """
    Q1 规格落地：将同一张报价单图片内的多个区块子表合并为单一 Sheet：
    - 1 个子表时直接保留原列结构；
    - 多个子表时首列增设「分类/区块」，统一对齐主表头（不同列按并集补齐空列，零丢行），并重映射 warnings 坐标。
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

    # 多区块：构建统一主表头并集（首列为 分类/区块）
    unified_headers: List[str] = ["分类/区块"]
    unified_types: List[str] = ["text"]
    header_to_col: Dict[str, int] = {}

    for sh in sheets:
        sh_headers = sh.get("headers") or []
        sh_types = sh.get("col_types") or []
        for idx, h in enumerate(sh_headers):
            h_clean = str(h or f"列{idx + 1}").strip() or f"列{idx + 1}"
            if h_clean not in header_to_col:
                header_to_col[h_clean] = len(unified_headers)
                unified_headers.append(h_clean)
                t = sh_types[idx] if idx < len(sh_types) else "text"
                unified_types.append(t if t in VALID_COL_TYPES else "text")

    total_cols = len(unified_headers)
    merged_rows: List[List[str]] = []
    merged_warnings: List[Dict[str, Any]] = []

    for s_idx, sh in enumerate(sheets):
        section_name = str(sh.get("name") or f"区块{s_idx + 1}").strip()
        sh_headers = [str(h or f"列{i + 1}").strip() or f"列{i + 1}" for i, h in enumerate(sh.get("headers") or [])]
        local_to_global = [header_to_col[h] for h in sh_headers]

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


def extract_tables_from_image(
    image_bytes: bytes,
    filename: str,
    content_type: Optional[str] = None,
    mode: str = "excel",
    force_demo: bool = False,
    custom_hint: str = "",
    merge_per_image: bool = True,
) -> Dict[str, Any]:
    """
    核心图像表格提取函数：
    - 配置了 GEMINI_API_KEY 且未开启 force_demo 时，调用 Gemini Interactions API + Structured Outputs；
    - 针对长报价单设足 40s 主请求超时（503/429 瞬切备用模型），并将单图多子表按 Q1 规范合并为单一 Sheet；
    - 未配置 Key 时自动平稳降级至本地结构化演示模式。
    """
    api_key = _get_gemini_api_key()
    if force_demo or not api_key:
        demo_res = _build_demo_result(filename, mode)
        if not merge_per_image:
            demo_res["sheets"] = demo_res["raw_sheets"]
        return demo_res

    mime_type = normalize_mime_type(filename, content_type)
    b64_data = base64.b64encode(image_bytes).decode("ascii")
    primary_model = get_active_model()
    # Free-Tier 友好容灾排序：主模型 -> 3.5-flash-lite -> 3.1-flash-lite -> 4-argon
    fallback_models = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-4-argon"]
    candidate_models: List[str] = []
    for m in [primary_model] + fallback_models:
        if m and m not in candidate_models:
            candidate_models.append(m)
    candidate_models = candidate_models[:3]

    instruction = (
        "You are an expert document and quotation-sheet OCR extraction engine. "
        "Extract every table/block visible in the image accurately into structured JSON. "
        "Set `title` to the main quotation header visible in the image (e.g. vendor name + date like '新军强 靓机 11.24'). "
        "Put top/edge vendor rules, validity notices, or disclaimers outside data rows into `document_notes` (do NOT mix them into table rows). "
        "Preserve exact text, model names, capacities, numbers, currency symbols, range strings (e.g. '3200-3300'), '+50', and leading zeros (e.g. '001042'). "
        "Every row in `rows` MUST have the exact same number of elements as `headers` (use empty string '' for blank cells). "
        "Set `col_types` accurately for each column (`text`, `number`, `currency`, `percent`, `date`). "
        "If any cell is blurry, occluded, or uncertain, still provide your best reading and record it in `warnings`. "
        "Also generate `plain_text` as a clean Markdown table."
    )
    if mode == "text":
        instruction += " Since mode is 'text', prioritize clean readable `plain_text` while also populating `sheets` if a table is present."
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
                timeout=40 if attempt_idx == 0 else 18,
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

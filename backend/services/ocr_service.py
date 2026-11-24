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
            "description": "Short descriptive title of the extracted document or table.",
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


def has_gemini_key() -> bool:
    return bool(_get_clean_env("GEMINI_API_KEY"))


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
        "sheets": normalized_sheets,
        "plain_text": plain_text,
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
    normalized["engine"] = "demo"
    normalized["is_demo"] = True
    normalized["notice"] = "当前为演示提取模式（未配置 GEMINI_API_KEY）。你仍可完整体验在线校对编辑与真实 .xlsx / .csv 导出。"
    if mode == "text":
        normalized["sheets"] = normalized["sheets"][:1]
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
) -> Dict[str, Any]:
    """
    核心图像表格提取函数：
    - 配置了 GEMINI_API_KEY 且未开启 force_demo 时，调用 Gemini 4 Hydrogen Interactions API + Structured Outputs；
    - 否则自动平稳降级至本地结构化演示模式，确保零 Key 开箱可用。
    """
    api_key = _get_clean_env("GEMINI_API_KEY")
    if force_demo or not api_key:
        return _build_demo_result(filename, mode)

    mime_type = normalize_mime_type(filename, content_type)
    b64_data = base64.b64encode(image_bytes).decode("ascii")
    model_name = get_active_model()

    instruction = (
        "You are an expert document and table OCR extraction engine. "
        "Extract every table visible in the image accurately into structured JSON. "
        "Preserve exact text, numbers, currency symbols, percentage signs, and leading zeros (e.g. '001042'). "
        "Every row in `rows` MUST have the exact same number of elements as `headers` (use empty string '' for blank cells). "
        "Set `col_types` accurately for each column (`text`, `number`, `currency`, `percent`, `date`). "
        "If any cell is blurry, occluded, or uncertain, still provide your best reading and record it in `warnings`. "
        "Also generate `plain_text` as a clean Markdown table."
    )
    if mode == "text":
        instruction += " Since mode is 'text', prioritize clean readable `plain_text` while also populating `sheets` if a table is present."
    if custom_hint.strip():
        instruction += f"\nAdditional user hint: {custom_hint.strip()}"

    payload = {
        "model": model_name,
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

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
        "Api-Revision": "2026-05-20",
    }

    try:
        resp = requests.post(GEMINI_INTERACTIONS_URL, json=payload, headers=headers, timeout=50)
    except requests.RequestException as exc:
        raise RuntimeError(f"连接 Gemini 视觉识别服务超时或网络异常：{type(exc).__name__}")

    if resp.status_code != 200:
        # 严禁在任何错误信息中回显 GEMINI_API_KEY 字符
        err_preview = resp.text[:240].replace(api_key, "***")
        if resp.status_code in (401, 403):
            raise RuntimeError("GEMINI_API_KEY 鉴权失败，请检查 Vercel 或 .env 中的密钥配置是否正确。")
        if resp.status_code == 429:
            raise RuntimeError("Gemini API 触发速率限制，请稍等几秒后重试，或切换为演示模式预览。")
        raise RuntimeError(f"Gemini 视觉识别接口返回异常 (HTTP {resp.status_code})：{err_preview}")

    raw_json = resp.json()
    text_out = _extract_text_from_interactions_response(raw_json).strip()
    if not text_out:
        raise RuntimeError("Gemini 模型未返回可解析的表格内容，请换一张更清晰的图片重试。")

    try:
        parsed = json.loads(text_out)
    except json.JSONDecodeError:
        # 兼容极少数带 markdown fence 的响应
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=re.IGNORECASE | re.DOTALL).strip()
        parsed = json.loads(cleaned)

    normalized = normalize_table_grid(parsed, fallback_name=Path(filename or "extracted").stem)
    normalized["engine"] = model_name
    normalized["is_demo"] = False
    normalized["notice"] = f"已通过 {model_name} 多模态引擎完成结构化表格识别"
    return normalized

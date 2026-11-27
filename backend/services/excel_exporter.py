import csv
import io
import re
import zipfile
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


def make_download_headers(filename: str) -> Dict[str, str]:
    """RFC 5987 兼容响应头，避免中文文件名在各浏览器下载时乱码或 latin-1 编码异常。"""
    stem, dot, ext = filename.rpartition(".")
    raw_stem = stem if dot else filename
    ascii_stem = re.sub(r"[^A-Za-z0-9._\-]", "_", raw_stem).strip("_.-") or "exported_tables"
    ascii_ext = re.sub(r"[^A-Za-z0-9]", "", ext) if dot else "xlsx"
    ascii_fallback = f"{ascii_stem}.{ascii_ext}"
    encoded = quote(filename, safe="")
    return {
        "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}",
        "Cache-Control": "no-store",
    }


def _safe_sheet_title(raw_name: str, used_names: set) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", "_", (raw_name or "Sheet").strip()) or "Sheet"
    base = cleaned[:28]
    candidate = base
    idx = 2
    while candidate.lower() in used_names:
        suffix = f"_{idx}"
        candidate = f"{base[: 31 - len(suffix)]}{suffix}"
        idx += 1
    used_names.add(candidate.lower())
    return candidate


def _display_width(text: str) -> int:
    width = 0
    for ch in text:
        width += 2 if ord(ch) > 127 else 1
    return width


_PURE_SHORT_NUMBER_RE = re.compile(r"^-?(?:0|[1-9]\d{0,10})(?:\.\d+)?$")


def _parse_typed_cell(raw_val: str, col_type: str) -> Tuple[Any, str]:
    """
    R2 数值与货币保真（全表智能数值化，彻底消除 Excel/WPS「数字为文本类型」绿三角，方便 XLOOKUP/VLOOKUP 匹配）：
    - 所有不含前导零且 <=11 位的纯数字（包含容量列 64/128/256/512、苹果型号列 13/14/15/16/17、价格列）一律存为真正的 Excel 数值；
    - 保留原始货币符号（¥ / $ / € / £）与千分位格式（如 ¥#,##0.00），不把财务金额退化成裸整数；
    - 含前导零编码（001042）、12 位以上长单号/税号、+50、3200-3300、1T、12+256 等保持文本原样。
    """
    text = (raw_val or "").strip()
    if not text:
        return "", "@"

    if col_type == "text":
        if _PURE_SHORT_NUMBER_RE.match(text):
            if "." in text:
                return float(text), "0.##"
            return int(text), "0"
        return text, "@"

    if col_type == "percent":
        cleaned = text.replace(",", "").replace("%", "").strip()
        try:
            num = float(cleaned)
            # 若原文本带 %，存为小数 0.15 以显示 15.0%
            if "%" in text:
                num = num / 100.0
            return num, "0.0%"
        except ValueError:
            return text, "@"

    if col_type in ("currency", "number"):
        if text.startswith("+"):
            return text, "@"
        sym_match = re.match(r"^([¥￥$€£])", text)
        cur_sym = "¥" if (sym_match and sym_match.group(1) in ("¥", "￥")) else (sym_match.group(1) if sym_match else "")
        has_comma = "," in text
        has_decimal = "." in text
        cleaned = re.sub(r"[¥￥$€£,\s]", "", text)
        digits_only = cleaned.lstrip("-")
        if re.match(r"^0\d+$", digits_only):
            return text, "@"
        if not cur_sym and not has_comma and digits_only.isdigit() and len(digits_only) >= 12:
            return text, "@"
        try:
            num = float(cleaned)
            if cur_sym:
                fmt = f"{cur_sym}#,##0.00" if (has_decimal or not num.is_integer()) else f"{cur_sym}#,##0"
                return (int(num) if num.is_integer() and not has_decimal else num), fmt
            if has_comma:
                fmt = "#,##0.00" if (has_decimal or not num.is_integer()) else "#,##0"
                return (int(num) if num.is_integer() and not has_decimal else num), fmt
            if num.is_integer():
                return int(num), "0"
            return num, "0.##"
        except ValueError:
            return text, "@"

    if _PURE_SHORT_NUMBER_RE.match(text):
        if "." in text:
            return float(text), "0.##"
        return int(text), "0"
    return text, "@"


def _is_summary_row_cells(row: List[str]) -> bool:
    for cell in row[:3]:
        s = str(cell or "").strip().lower()
        if not s:
            continue
        if s in {"合计", "总计", "小计", "汇总", "共计", "total", "subtotal", "grand total"} or any(
            s.startswith(p) for p in ("合计", "总计", "小计", "汇总", "共计", "total:", "subtotal:")
        ):
            return True
    return False


def _infer_style_preset(sheet_data: Dict[str, Any]) -> str:
    explicit = str(sheet_data.get("style_preset") or "").strip().lower()
    if explicit in ("quotation", "general"):
        return explicit
    headers_text = " ".join(str(h or "") for h in (sheet_data.get("headers") or []))
    if any(k in headers_text for k in ("充新", "靓机", "小花", "大花", "外爆", "内爆", "容量", "型号", "系列")):
        return "quotation"
    return "general"


def _populate_worksheet(ws: Any, sheet_data: Dict[str, Any]) -> None:
    headers: List[str] = [str(h) for h in (sheet_data.get("headers") or ["列1"])]
    rows: List[List[str]] = sheet_data.get("rows") or []
    col_types: List[str] = sheet_data.get("col_types") or ["text"] * len(headers)
    merges: List[Dict[str, int]] = sheet_data.get("merges") or []
    style_preset = _infer_style_preset(sheet_data)

    if style_preset == "general":
        # 通用多场景表格（财务/发票/库存/考勤）：商务简洁灰蓝表头 + 柔和浅灰斑马纹 + 合计行加粗底纹
        header_fill = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
        header_font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
        zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        summary_fill = PatternFill(start_color="EEF2FF", end_color="EEF2FF", fill_type="solid")
        body_font = Font(name="微软雅黑", size=10, color="0F172A")
        price_font = Font(name="微软雅黑", size=10, bold=False, color="0F172A")
        summary_font = Font(name="微软雅黑", size=10, bold=True, color="0F172A")
        thin_side = Side(style="thin", color="CBD5E1")
    else:
        # 报价单专精样式：WPS 清爽苹果绿表头 + 浅绿斑马纹（不合并型号单元格，兼容筛选/VLOOKUP）
        header_fill = PatternFill(start_color="A9D08E", end_color="A9D08E", fill_type="solid")
        header_font = Font(name="微软雅黑", size=11, bold=True, color="000000")
        zebra_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
        summary_fill = PatternFill(start_color="D9EAD3", end_color="D9EAD3", fill_type="solid")
        body_font = Font(name="微软雅黑", size=10, color="0F172A")
        price_font = Font(name="微软雅黑", size=10, bold=True, color="0F172A")
        summary_font = Font(name="微软雅黑", size=10, bold=True, color="0F172A")
        thin_side = Side(style="thin", color="8EA99B")
    cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    col_widths = [_display_width(h) + 4 for h in headers]

    def _is_note_column(header_text: str) -> bool:
        return any(k in header_text for k in ("备注", "蓝光", "彩点", "面容", "拆修", "扣费", "说明"))

    def _is_capacity_column(header_text: str) -> bool:
        return any(k in header_text for k in ("容量", "内存", "配置", "规格"))

    def _is_non_bold_id_column(header_text: str) -> bool:
        return _is_capacity_column(header_text) or any(
            k in header_text for k in ("型号", "机型", "系列", "编码", "代号", "序号", "编号")
        )

    # 写入第 1 行苹果绿表头
    for c_idx, h_text in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=c_idx, value=h_text)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = cell_border
    ws.row_dimensions[1].height = 30

    # 先估算列宽，再写入数据行并自适应计算行高
    for row in rows:
        for c_idx in range(1, len(headers) + 1):
            raw_cell = str(row[c_idx - 1]) if c_idx - 1 < len(row) else ""
            # 多行备注按最长单行估算列宽，避免把备注列撑到太宽
            max_line_w = max((_display_width(line) for line in raw_cell.splitlines()), default=0)
            col_widths[c_idx - 1] = max(col_widths[c_idx - 1], max_line_w + 3)

    final_widths = [min(max(w, 10), 40 if _is_note_column(headers[i]) else 28) for i, w in enumerate(col_widths)]

    section_header_rows = set(int(x) for x in (sheet_data.get("section_header_rows") or []))

    for r_idx, row in enumerate(rows, start=2):
        is_section_header = (r_idx - 2) in section_header_rows
        is_summary = (not is_section_header) and _is_summary_row_cells(row)
        max_lines = 1
        for c_idx in range(1, len(headers) + 1):
            raw_cell = str(row[c_idx - 1]) if c_idx - 1 < len(row) else ""
            c_type = col_types[c_idx - 1] if c_idx - 1 < len(col_types) else "text"
            h_name = headers[c_idx - 1]

            if is_section_header:
                cell = ws.cell(row=r_idx, column=c_idx, value=raw_cell)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = cell_border
                continue

            effective_type = "text" if (style_preset == "quotation" and _is_capacity_column(h_name)) else c_type
            typed_val, num_fmt = _parse_typed_cell(raw_cell, effective_type)
            cell = ws.cell(row=r_idx, column=c_idx, value=typed_val)
            cell.border = cell_border

            if num_fmt and num_fmt != "@":
                cell.number_format = num_fmt
                if is_summary:
                    cell.font = summary_font
                elif _is_non_bold_id_column(h_name):
                    cell.font = body_font
                else:
                    cell.font = price_font
                cell.alignment = Alignment(
                    horizontal="right" if (style_preset == "general" and ("," in num_fmt or any(s in num_fmt for s in ("¥", "$", "€", "£", "%")))) else "center",
                    vertical="center",
                )
            elif _is_note_column(h_name) or "\n" in raw_cell or _display_width(raw_cell) > 18:
                cell.font = summary_font if is_summary else body_font
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
                col_w = max(8, final_widths[c_idx - 1] - 2)
                line_cnt = 0
                for line in (raw_cell.splitlines() or [""]):
                    line_cnt += max(1, (_display_width(line) + col_w - 1) // col_w)
                max_lines = max(max_lines, line_cnt)
            else:
                cell.font = summary_font if is_summary else body_font
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            if is_summary:
                cell.fill = summary_fill
            elif r_idx % 2 == 1:
                cell.fill = zebra_fill

        ws.row_dimensions[r_idx].height = 28 if is_section_header else max(22, min(78, max_lines * 15 + 6))

    # 合并单元格（跳过非锚点格，避免 openpyxl MergedCell 只读报错；数据行偏移 +2）
    for m in merges:
        try:
            sr = int(m["start_row"]) + 2
            sc = int(m["start_col"]) + 1
            er = int(m["end_row"]) + 2
            ec = int(m["end_col"]) + 1
            if 2 <= sr <= er <= len(rows) + 1 and 1 <= sc <= ec <= len(headers):
                ws.merge_cells(start_row=sr, start_column=sc, end_row=er, end_column=ec)
        except Exception:
            continue

    for c_idx, width in enumerate(final_widths, start=1):
        ws.column_dimensions[get_column_letter(c_idx)].width = width

    ws.freeze_panes = "A2"


def build_xlsx_bytes(sheets: List[Dict[str, Any]]) -> bytes:
    """将多个结构化 Sheet 写入单一 .xlsx 内存流。"""
    wb = Workbook()
    default_ws = wb.active
    used_names: set = set()

    if not sheets:
        sheets = [{"name": "Sheet1", "headers": ["空表"], "col_types": ["text"], "rows": [[""]]}]

    for idx, sheet in enumerate(sheets):
        ws = default_ws if idx == 0 else wb.create_sheet()
        ws.title = _safe_sheet_title(str(sheet.get("name") or f"Sheet{idx + 1}"), used_names)
        _populate_worksheet(ws, sheet)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def build_csv_bytes(sheet: Dict[str, Any]) -> bytes:
    """生成带 UTF-8 BOM 的 CSV（确保 Windows Excel 双击不乱码）。"""
    out = io.StringIO()
    writer = csv.writer(out)
    headers = [str(h) for h in (sheet.get("headers") or [])]
    writer.writerow(headers)
    for row in (sheet.get("rows") or []):
        writer.writerow([str(c if c is not None else "") for c in row])
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def _safe_filename_stem(raw_name: str, used_names: set) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\[\]]+", "_", (raw_name or "报价单").strip()).strip("_.") or "报价单"
    base = cleaned[:48]
    candidate = base
    idx = 2
    while candidate.lower() in used_names:
        suffix = f"_{idx}"
        candidate = f"{base[: 56 - len(suffix)]}{suffix}"
        idx += 1
    used_names.add(candidate.lower())
    return candidate


def build_single_xlsx_bytes(sheet: Dict[str, Any]) -> bytes:
    """将单份报价单写为独立 .xlsx 内存流。"""
    return build_xlsx_bytes([sheet])


def build_batch_zip_bytes(sheets: List[Dict[str, Any]]) -> bytes:
    """将多份报价单分别生成独立 .xlsx，平铺打包为单个 .zip 压缩包。"""
    zip_buf = io.BytesIO()
    used_files: set = set()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, sh in enumerate(sheets):
            raw_title = str(sh.get("export_filename") or sh.get("name") or f"报价单_{idx + 1}")
            fname = _safe_filename_stem(raw_title, used_files)
            zf.writestr(f"{fname}.xlsx", build_xlsx_bytes([sh]))
    zip_buf.seek(0)
    return zip_buf.getvalue()


def export_tables_package(
    sheets: List[Dict[str, Any]],
    output_mode: str = "all_in_one",
    file_format: str = "xlsx",
    base_filename: str = "image-to-excel",
) -> Tuple[bytes, str, str]:
    """
    统一导出入口：
    - format='csv'：单表导出 .csv，多表打包 .zip
    - format='xlsx' + output_mode='all_in_one'：合并为一个多 Sheet .xlsx
    - format='xlsx' + output_mode='per_image'：单表直接 .xlsx，多表自动打包 .zip（每表独立工作簿）
    返回：(bytes_data, download_filename, media_type)
    """
    safe_base = re.sub(r"[\\/:*?\"<>|\[\]]+", "_", (base_filename or "报价单").strip()).strip("_.") or "报价单"
    if not sheets:
        sheets = [{"name": "Sheet1", "headers": ["内容"], "col_types": ["text"], "rows": [[""]]}]

    if file_format == "csv":
        if len(sheets) == 1:
            return (
                build_csv_bytes(sheets[0]),
                f"{safe_base}.csv",
                "text/csv; charset=utf-8",
            )
        zip_buf = io.BytesIO()
        used_files: set = set()
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for idx, sh in enumerate(sheets):
                raw_title = str(sh.get("export_filename") or sh.get("name") or f"table_{idx + 1}")
                fname = _safe_filename_stem(raw_title, used_files)
                zf.writestr(f"{fname}.csv", build_csv_bytes(sh))
        zip_buf.seek(0)
        return (zip_buf.getvalue(), f"{safe_base}_csv打包.zip", "application/zip")

    if output_mode == "per_image" and len(sheets) > 1:
        return (build_batch_zip_bytes(sheets), f"{safe_base}_报价单批量打包.zip", "application/zip")

    return (
        build_xlsx_bytes(sheets),
        f"{safe_base}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

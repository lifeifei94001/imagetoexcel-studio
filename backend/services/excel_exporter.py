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


def _parse_typed_cell(raw_val: str, col_type: str) -> Tuple[Any, str]:
    """
    R2 数值保真：
    - 对 number / currency / percent 列尽力转为真实数字并保留 Excel 格式；
    - 含前导零编码（如 001042）保持文本，防止丢失前缀零。
    """
    text = (raw_val or "").strip()
    if not text:
        return "", "@"

    if col_type == "text":
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

    if col_type == "currency":
        if text.startswith("+"):
            return text, "@"
        currency_symbol = "¥"
        for sym in ("¥", "$", "€", "£"):
            if sym in text:
                currency_symbol = sym
                break
        cleaned = re.sub(r"[¥$€£,\s]", "", text)
        try:
            num = float(cleaned)
            fmt = f"{currency_symbol}#,##0.00"
            return num, fmt
        except ValueError:
            return text, "@"

    if col_type == "number":
        if text.startswith("+"):
            return text, "@"
        cleaned = text.replace(",", "").strip()
        # 前导零编号保持文本
        if re.match(r"^0\d+$", cleaned):
            return text, "@"
        try:
            if "." in cleaned:
                return float(cleaned), "#,##0.00"
            return int(cleaned), "#,##0"
        except ValueError:
            return text, "@"

    return text, "@"


def _populate_worksheet(ws: Any, sheet_data: Dict[str, Any]) -> None:
    headers: List[str] = [str(h) for h in (sheet_data.get("headers") or ["列1"])]
    rows: List[List[str]] = sheet_data.get("rows") or []
    col_types: List[str] = sheet_data.get("col_types") or ["text"] * len(headers)
    merges: List[Dict[str, int]] = sheet_data.get("merges") or []

    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    body_font = Font(name="Arial", size=10, color="0F172A")
    thin_side = Side(style="thin", color="CBD5E1")
    cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    col_widths = [_display_width(h) + 4 for h in headers]

    # 写入第 1 行表头
    for c_idx, h_text in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=c_idx, value=h_text)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = cell_border
    ws.row_dimensions[1].height = 26

    # 写入数据行（从第 2 行开始）
    for r_idx, row in enumerate(rows, start=2):
        for c_idx in range(1, len(headers) + 1):
            raw_cell = str(row[c_idx - 1]) if c_idx - 1 < len(row) else ""
            c_type = col_types[c_idx - 1] if c_idx - 1 < len(col_types) else "text"
            typed_val, num_fmt = _parse_typed_cell(raw_cell, c_type)
            cell = ws.cell(row=r_idx, column=c_idx, value=typed_val)
            cell.font = body_font
            cell.border = cell_border
            if num_fmt and num_fmt != "@":
                cell.number_format = num_fmt
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            if r_idx % 2 == 1:
                cell.fill = zebra_fill
            col_widths[c_idx - 1] = max(col_widths[c_idx - 1], _display_width(raw_cell) + 3)

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

    for c_idx, width in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(c_idx)].width = min(max(width, 11), 46)

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

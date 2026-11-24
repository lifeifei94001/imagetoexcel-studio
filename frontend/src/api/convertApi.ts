import axios from "axios";

export type ColumnSemanticType = "text" | "number" | "currency" | "percent" | "date";

export interface CellWarning {
  row: number;
  col: number;
  reason: string;
}

export interface MergeRange {
  start_row: number;
  start_col: number;
  end_row: number;
  end_col: number;
}

export interface ExtractedSheet {
  name: string;
  headers: string[];
  col_types: ColumnSemanticType[];
  rows: string[][];
  merges: MergeRange[];
  warnings: CellWarning[];
  source_filename?: string;
  source_index?: number;
}

export interface QuotaInfo {
  quota_date: string;
  used_count: number;
  daily_limit: number;
  remaining: number;
  gemini_configured?: boolean;
  model?: string;
}

export interface ExtractResponse {
  title: string;
  mode: string;
  output_mode: "all_in_one" | "per_image";
  engine: string;
  is_demo: boolean;
  notice: string;
  sheets: ExtractedSheet[];
  plain_text: string;
  quota: QuotaInfo;
}

export interface ConversionRecord {
  id: number;
  filename: string;
  file_size_bytes: number;
  mode: string;
  engine: string;
  sheet_count: number;
  row_count: number;
  col_count: number;
  warning_count: number;
  status: string;
  created_at?: string;
}

const http = axios.create({
  baseURL: "/api",
  timeout: 65000,
});

export function extractApiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail || err.response?.data?.message;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (err.code === "ECONNABORTED") return "识别请求超时，请尝试裁剪大图或减少单批上传数量";
    return err.message || "网络请求失败";
  }
  return err instanceof Error ? err.message : "发生未知异常";
}

export async function fetchHealth() {
  const { data } = await http.get<{
    status: string;
    db_mode: string;
    gemini_configured: boolean;
    model: string;
  }>("/health");
  return data;
}

export async function fetchQuota(): Promise<QuotaInfo> {
  const { data } = await http.get<QuotaInfo>("/quota");
  return data;
}

export async function fetchHistory(): Promise<ConversionRecord[]> {
  const { data } = await http.get<ConversionRecord[]>("/history");
  return data;
}

export async function deleteHistoryRecord(id: number): Promise<void> {
  await http.delete(`/history/${id}`);
}

export async function extractImages(params: {
  files: File[];
  mode: "excel" | "text";
  outputMode: "all_in_one" | "per_image";
  forceDemo?: boolean;
  customHint?: string;
}): Promise<ExtractResponse> {
  const formData = new FormData();
  for (const f of params.files) {
    formData.append("files", f);
  }
  formData.append("mode", params.mode);
  formData.append("output_mode", params.outputMode);
  formData.append("force_demo", String(Boolean(params.forceDemo)));
  if (params.customHint?.trim()) {
    formData.append("custom_hint", params.customHint.trim());
  }
  const { data } = await http.post<ExtractResponse>("/extract", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function downloadExportedTables(params: {
  sheets: ExtractedSheet[];
  outputMode: "all_in_one" | "per_image";
  format: "xlsx" | "csv";
  filename: string;
}): Promise<string> {
  const resp = await http.post(
    "/export",
    {
      sheets: params.sheets,
      output_mode: params.outputMode,
      format: params.format,
      filename: params.filename,
    },
    { responseType: "blob" }
  );

  const disposition = resp.headers["content-disposition"] || "";
  let dlName = `${params.filename || "image-to-excel"}.${params.format}`;
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    dlName = decodeURIComponent(utf8Match[1]);
  } else {
    const asciiMatch = disposition.match(/filename="?([^";]+)"?/i);
    if (asciiMatch?.[1]) dlName = asciiMatch[1];
  }

  const blobUrl = URL.createObjectURL(resp.data);
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = dlName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 1500);
  return dlName;
}

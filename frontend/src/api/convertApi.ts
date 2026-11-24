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
  export_filename?: string;
  headers: string[];
  col_types: ColumnSemanticType[];
  rows: string[][];
  merges: MergeRange[];
  warnings: CellWarning[];
  source_filename?: string;
}

export interface QuotationGroup {
  source_filename: string;
  quote_title: string;
  document_notes: string;
  engine: string;
  is_demo: boolean;
  notice: string;
  merged_sheet: ExtractedSheet;
  raw_sheets: ExtractedSheet[];
  plain_text?: string;
}

export interface QuotaInfo {
  quota_date: string;
  used_count: number;
  daily_limit: number;
  remaining: number;
  gemini_configured?: boolean;
  model?: string;
  b2_configured?: boolean;
}

export interface ExtractResponse {
  title: string;
  mode: string;
  output_mode: "all_in_one" | "per_image";
  engine: string;
  is_demo: boolean;
  notice: string;
  sheets: ExtractedSheet[];
  quotations?: QuotationGroup[];
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

export interface BatchJobItem {
  id: number;
  job_uid: string;
  item_index: number;
  source_filename: string;
  quote_title: string;
  engine: string;
  is_demo: boolean;
  row_count: number;
  col_count: number;
  warning_count: number;
  xlsx_b2_key: string;
  xlsx_filename: string;
  status: string;
  error_message: string;
  created_at?: string;
}

export interface BatchJob {
  id: number;
  job_uid: string;
  title: string;
  mode: string;
  output_mode: "all_in_one" | "per_image";
  merge_per_image: boolean;
  total_items: number;
  completed_items: number;
  failed_items: number;
  status: string;
  zip_b2_key: string;
  zip_filename: string;
  json_b2_key: string;
  storage_backend: string;
  created_at?: string;
  updated_at?: string;
  items: BatchJobItem[];
}

const http = axios.create({
  baseURL: "/api",
  timeout: 65000,
});

export function extractApiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail || err.response?.data?.message;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (err.code === "ECONNABORTED") return "单张长图识别超过 65 秒超时，请尝试适当裁剪后重试";
    return err.message || "网络请求失败";
  }
  return err instanceof Error ? err.message : "发生未知异常";
}

function triggerBlobDownload(blob: Blob, fallbackName: string, contentDisposition?: string): string {
  let dlName = fallbackName;
  if (contentDisposition) {
    const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match?.[1]) {
      dlName = decodeURIComponent(utf8Match[1]);
    } else {
      const asciiMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
      if (asciiMatch?.[1]) dlName = asciiMatch[1];
    }
  }
  const blobUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = dlName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 1500);
  return dlName;
}

export async function fetchHealth() {
  const { data } = await http.get<{
    status: string;
    db_mode: string;
    gemini_configured: boolean;
    model: string;
    b2_configured?: boolean;
    b2_bucket?: string;
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

export async function createBatchJob(params: {
  title: string;
  mode: "excel" | "text";
  outputMode: "all_in_one" | "per_image";
  mergePerImage: boolean;
  totalItems: number;
}): Promise<BatchJob> {
  const { data } = await http.post<BatchJob>("/jobs", {
    title: params.title,
    mode: params.mode,
    output_mode: params.outputMode,
    merge_per_image: params.mergePerImage,
    total_items: params.totalItems,
  });
  return data;
}

export async function processBatchJobItem(params: {
  jobUid: string;
  file: File;
  itemIndex: number;
  mode: "excel" | "text";
  forceDemo: boolean;
  customHint?: string;
  mergePerImage: boolean;
}): Promise<{
  job_uid: string;
  item: BatchJobItem;
  quotation: QuotationGroup;
  quota: QuotaInfo;
}> {
  const formData = new FormData();
  formData.append("file", params.file);
  formData.append("item_index", String(params.itemIndex));
  formData.append("mode", params.mode);
  formData.append("force_demo", String(params.forceDemo));
  formData.append("merge_per_image", String(params.mergePerImage));
  if (params.customHint?.trim()) {
    formData.append("custom_hint", params.customHint.trim());
  }
  const { data } = await http.post(`/jobs/${params.jobUid}/process-item`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function finalizeBatchJob(params: {
  jobUid: string;
  quotations: QuotationGroup[];
  mergePerImage: boolean;
  outputMode: "all_in_one" | "per_image";
}): Promise<BatchJob> {
  const { data } = await http.post<BatchJob>(`/jobs/${params.jobUid}/finalize`, {
    quotations: params.quotations,
    merge_per_image: params.mergePerImage,
    output_mode: params.outputMode,
  });
  return data;
}

export async function fetchBatchJobs(): Promise<BatchJob[]> {
  const { data } = await http.get<BatchJob[]>("/jobs");
  return data;
}

export async function downloadJobArtifact(params: {
  jobUid: string;
  itemId?: number;
  fallbackFilename: string;
}): Promise<string> {
  const resp = await http.get(`/jobs/${params.jobUid}/download`, {
    params: params.itemId !== undefined ? { item_id: params.itemId } : undefined,
    responseType: "blob",
  });
  return triggerBlobDownload(resp.data, params.fallbackFilename, resp.headers["content-disposition"]);
}

export async function loadJobIntoEditor(jobUid: string): Promise<{
  job_uid: string;
  merge_per_image: boolean;
  output_mode: "all_in_one" | "per_image";
  quotations: QuotationGroup[];
}> {
  const { data } = await http.get(`/jobs/${jobUid}/load`);
  return data;
}

export async function deleteBatchJob(jobUid: string): Promise<void> {
  await http.delete(`/jobs/${jobUid}`);
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
  return triggerBlobDownload(
    resp.data,
    `${params.filename || "报价单导出"}.${params.format}`,
    resp.headers["content-disposition"]
  );
}

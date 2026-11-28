import type { DocScenarioType } from "../api/convertApi";

export interface CheckpointFileEntry {
  id: string;
  name: string;
  type: string;
  size: number;
  blob: Blob;
}

export interface SliceCheckpoint {
  totalSlices: number;
  seamMode: "smart_seam" | "overlap" | "single";
  hasHeaderAnchor: boolean;
  inheritedHeaderHint: string;
  completedSlices: Record<number, Record<string, unknown>>;
}

export interface BatchCheckpointSettings {
  mode: "excel" | "text";
  docType: DocScenarioType;
  outputMode: "all_in_one" | "per_image";
  mergePerImage: boolean;
  pricesOnly: boolean;
  dualVerify: boolean;
  modelTier: "fast" | "accurate";
  forceDemo: boolean;
  customHint: string;
}

export interface BatchCheckpointRecord {
  version: 1;
  jobUid: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  stoppedByUser: boolean;
  settings: BatchCheckpointSettings;
  files: CheckpointFileEntry[];
  itemStatuses: Array<"pending" | "processing" | "completed" | "failed">;
  itemMessages: string[];
  itemQuoteTitles: string[];
  sliceProgress: Record<number, SliceCheckpoint>;
}

export interface RecentBatchArchive {
  version: 1;
  jobUid: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  totalBytes: number;
  settings: BatchCheckpointSettings;
  files: CheckpointFileEntry[];
  itemQuoteTitles: string[];
}

const DB_NAME = "i2e_resumable_batch_db_v1";
const STORE_NAME = "checkpoints";
const ACTIVE_KEY = "active_batch";
const LAST_ARCHIVE_KEY = "last_completed_batch";

function openCheckpointDb(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    try {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

/** 持久化当前批处理断点（含原图二进制 Blob 与切片级进度）到浏览器 IndexedDB。 */
export async function saveBatchCheckpoint(record: BatchCheckpointRecord): Promise<boolean> {
  const db = await openCheckpointDb();
  if (!db) return false;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readwrite");
      const store = tx.objectStore(STORE_NAME);
      const safeRecord: BatchCheckpointRecord = {
        ...record,
        updatedAt: Date.now(),
      };
      store.put(safeRecord, ACTIVE_KEY);
      tx.oncomplete = () => {
        db.close();
        resolve(true);
      };
      tx.onerror = () => {
        db.close();
        resolve(false);
      };
    } catch {
      db.close();
      resolve(false);
    }
  });
}

/** 读取未完成的批处理断点记录；若无记录或已全部完成返回 null。 */
export async function loadBatchCheckpoint(): Promise<BatchCheckpointRecord | null> {
  const db = await openCheckpointDb();
  if (!db) return null;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readonly");
      const store = tx.objectStore(STORE_NAME);
      const req = store.get(ACTIVE_KEY);
      req.onsuccess = () => {
        db.close();
        const data = req.result as BatchCheckpointRecord | undefined;
        if (!data || data.version !== 1 || !Array.isArray(data.files) || data.files.length === 0) {
          resolve(null);
          return;
        }
        resolve(data);
      };
      req.onerror = () => {
        db.close();
        resolve(null);
      };
    } catch {
      db.close();
      resolve(null);
    }
  });
}

/** 彻底清理本地断点缓存（任务全部完成或用户点击作废时调用）。 */
export async function clearBatchCheckpoint(): Promise<void> {
  const db = await openCheckpointDb();
  if (!db) return;
  await new Promise<void>((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).delete(ACTIVE_KEY);
      tx.oncomplete = () => {
        db.close();
        resolve();
      };
      tx.onerror = () => {
        db.close();
        resolve();
      };
    } catch {
      db.close();
      resolve();
    }
  });
}

/**
 * 持久化「最近 1 批」原图缓存至本地 IndexedDB（自动覆盖上一批，始终最多仅占最近 1 批空间）。
 * 用于单张原位重跑、整批一键装回队列，以及刷新/重开浏览器后恢复左侧原图对照预览。
 */
export async function saveRecentBatchArchive(archive: RecentBatchArchive): Promise<boolean> {
  const db = await openCheckpointDb();
  if (!db) return false;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readwrite");
      const store = tx.objectStore(STORE_NAME);
      const totalBytes = archive.files.reduce((acc, f) => acc + (f.size || f.blob?.size || 0), 0);
      const safeArchive: RecentBatchArchive = {
        ...archive,
        totalBytes,
        updatedAt: Date.now(),
      };
      store.put(safeArchive, LAST_ARCHIVE_KEY);
      tx.oncomplete = () => {
        db.close();
        resolve(true);
      };
      tx.onerror = () => {
        db.close();
        resolve(false);
      };
    } catch {
      db.close();
      resolve(false);
    }
  });
}

/** 读取最近 1 批原图本地缓存；若无记录返回 null。 */
export async function loadRecentBatchArchive(): Promise<RecentBatchArchive | null> {
  const db = await openCheckpointDb();
  if (!db) return null;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readonly");
      const store = tx.objectStore(STORE_NAME);
      const req = store.get(LAST_ARCHIVE_KEY);
      req.onsuccess = () => {
        db.close();
        const data = req.result as RecentBatchArchive | undefined;
        if (!data || data.version !== 1 || !Array.isArray(data.files) || data.files.length === 0) {
          resolve(null);
          return;
        }
        resolve(data);
      };
      req.onerror = () => {
        db.close();
        resolve(null);
      };
    } catch {
      db.close();
      resolve(null);
    }
  });
}

/** 清空最近 1 批原图本地缓存，释放浏览器磁盘占用。 */
export async function clearRecentBatchArchive(): Promise<void> {
  const db = await openCheckpointDb();
  if (!db) return;
  await new Promise<void>((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).delete(LAST_ARCHIVE_KEY);
      tx.oncomplete = () => {
        db.close();
        resolve();
      };
      tx.onerror = () => {
        db.close();
        resolve();
      };
    } catch {
      db.close();
      resolve();
    }
  });
}

/** 将 IndexedDB 中保存的 Blob 还原为标准 File 对象以复用完整切片/上传链路。 */
export function checkpointEntryToFile(entry: CheckpointFileEntry): File {
  if (entry.blob instanceof File && entry.blob.name === entry.name) {
    return entry.blob;
  }
  return new File([entry.blob], entry.name, {
    type: entry.type || entry.blob.type || "image/png",
  });
}

/** 统计断点记录里的已完成图片数与缓存切片总数，用于顶部恢复横幅展示。 */
export function summarizeCheckpoint(cp: BatchCheckpointRecord): {
  total: number;
  completed: number;
  remaining: number;
  cachedSliceCount: number;
} {
  const total = cp.files.length;
  const completed = cp.itemStatuses.filter((s) => s === "completed").length;
  let cachedSliceCount = 0;
  for (const [idxStr, sp] of Object.entries(cp.sliceProgress || {})) {
    const idx = Number(idxStr);
    if (cp.itemStatuses[idx] !== "completed" && sp?.completedSlices) {
      cachedSliceCount += Object.keys(sp.completedSlices).length;
    }
  }
  return {
    total,
    completed,
    remaining: Math.max(0, total - completed),
    cachedSliceCount,
  };
}

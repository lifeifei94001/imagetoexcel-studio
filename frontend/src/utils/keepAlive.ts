/**
 * 长耗时批量任务的浏览器端保活与容灾引擎：
 * 1. Inline Web Worker 计时器（后台切标签页时不触发 Chrome/Edge 主线程 60s 节流）；
 * 2. Web Locks API 排他锁（告知 Chrome/Edge 内存节省程序与休眠标签页「本页正在运行关键任务，禁止丢弃休眠」）；
 * 3. Screen Wake Lock API（运行期间阻止电脑自动息屏睡眠，完成后自动释放）；
 * 4. 断网自动暂停 + 每 5 秒探测 `/api/health` 恢复后无缝续跑（最长静候 30 分钟，支持 AbortSignal 秒级中断）。
 */

export class TaskAbortedError extends Error {
  constructor(message = "任务已由用户手动停止") {
    super(message);
    this.name = "TaskAbortedError";
  }
}

interface PendingTimer {
  resolve: () => void;
  reject: (err: Error) => void;
  cleanup?: () => void;
}

let timerWorker: Worker | null = null;
let timerWorkerFailed = false;
let nextTimerId = 1;
const pendingTimers = new Map<number, PendingTimer>();

function getTimerWorker(): Worker | null {
  if (timerWorkerFailed || typeof Worker === "undefined" || typeof Blob === "undefined") {
    return null;
  }
  if (timerWorker) return timerWorker;
  try {
    const workerCode = `
      const timers = new Map();
      self.onmessage = (e) => {
        const { type, id, ms } = e.data || {};
        if (type === 'start') {
          const t = setTimeout(() => {
            timers.delete(id);
            self.postMessage({ id });
          }, Math.max(0, Number(ms) || 0));
          timers.set(id, t);
        } else if (type === 'cancel') {
          const t = timers.get(id);
          if (t !== undefined) {
            clearTimeout(t);
            timers.delete(id);
          }
        }
      };
    `;
    const blobUrl = URL.createObjectURL(new Blob([workerCode], { type: "application/javascript" }));
    const w = new Worker(blobUrl);
    URL.revokeObjectURL(blobUrl);
    w.onmessage = (e: MessageEvent<{ id?: number }>) => {
      const id = e.data?.id;
      if (typeof id !== "number") return;
      const entry = pendingTimers.get(id);
      if (entry) {
        pendingTimers.delete(id);
        entry.cleanup?.();
        entry.resolve();
      }
    };
    w.onerror = () => {
      timerWorkerFailed = true;
      timerWorker = null;
    };
    timerWorker = w;
    return w;
  } catch {
    timerWorkerFailed = true;
    return null;
  }
}

/** 抗后台标签降频的睡眠等待，支持 AbortSignal 立即中断。 */
export function workerSleep(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) {
    return Promise.reject(new TaskAbortedError());
  }
  if (ms <= 0) return Promise.resolve();

  const w = getTimerWorker();
  if (!w) {
    return new Promise<void>((resolve, reject) => {
      const onAbort = () => {
        clearTimeout(t);
        reject(new TaskAbortedError());
      };
      const t = window.setTimeout(() => {
        signal?.removeEventListener("abort", onAbort);
        resolve();
      }, ms);
      signal?.addEventListener("abort", onAbort, { once: true });
    });
  }

  return new Promise<void>((resolve, reject) => {
    const id = nextTimerId++;
    const onAbort = () => {
      pendingTimers.delete(id);
      try {
        w.postMessage({ type: "cancel", id });
      } catch {
        // 忽略 worker 终止异常
      }
      reject(new TaskAbortedError());
    };
    const cleanup = () => {
      signal?.removeEventListener("abort", onAbort);
    };
    pendingTimers.set(id, { resolve, reject, cleanup });
    signal?.addEventListener("abort", onAbort, { once: true });
    try {
      w.postMessage({ type: "start", id, ms });
    } catch {
      pendingTimers.delete(id);
      cleanup();
      // 回退到主线程定时器
      const fallbackT = window.setTimeout(resolve, ms);
      signal?.addEventListener(
        "abort",
        () => {
          clearTimeout(fallbackT);
          reject(new TaskAbortedError());
        },
        { once: true }
      );
    }
  });
}

export interface BatchKeepAliveHandle {
  wakeLockActive: boolean;
  webLockActive: boolean;
  release: () => void;
}

/**
 * 批量运行期间申请浏览器防休眠与防标签丢弃保护：
 * - Web Locks API：提示 Edge/Chrome 不要将当前标签丢入「休眠标签页 / 内存节省程序」；
 * - Screen Wake Lock API：防止无人值守运行 10+ 分钟时系统自动息屏进入睡眠。
 */
export async function acquireBatchKeepAlive(): Promise<BatchKeepAliveHandle> {
  let released = false;
  let releaseWebLock: (() => void) | null = null;
  let webLockActive = false;
  let wakeLockActive = false;
  let wakeSentinel: { release: () => Promise<void> } | null = null;

  // 1. Web Lock 排他锁（在 Chrome/Edge 中可有效阻止后台标签页自动休眠）
  try {
    const navLocks = (navigator as Navigator & {
      locks?: {
        request: (
          name: string,
          opts: { mode: "exclusive"; ifAvailable?: boolean },
          cb: (lock: unknown) => Promise<void>
        ) => Promise<void>;
      };
    }).locks;
    if (navLocks?.request) {
      webLockActive = true;
      void navLocks
        .request(
          `i2e_batch_keepalive_${Date.now()}`,
          { mode: "exclusive" },
          () =>
            new Promise<void>((resolve) => {
              if (released) {
                resolve();
              } else {
                releaseWebLock = resolve;
              }
            })
        )
        .catch(() => {
          webLockActive = false;
        });
    }
  } catch {
    webLockActive = false;
  }

  // 2. Screen Wake Lock（切回前台时自动重新申请）
  const requestWake = async () => {
    if (released || document.visibilityState !== "visible") return;
    try {
      const navWake = (navigator as Navigator & {
        wakeLock?: { request: (type: "screen") => Promise<{ release: () => Promise<void> }> };
      }).wakeLock;
      if (navWake?.request) {
        wakeSentinel = await navWake.request("screen");
        wakeLockActive = true;
      }
    } catch {
      wakeLockActive = false;
    }
  };
  await requestWake();

  const onVisibility = () => {
    if (!released && document.visibilityState === "visible" && !wakeSentinel) {
      void requestWake();
    }
  };
  document.addEventListener("visibilitychange", onVisibility);

  return {
    get wakeLockActive() {
      return wakeLockActive;
    },
    get webLockActive() {
      return webLockActive;
    },
    release() {
      if (released) return;
      released = true;
      document.removeEventListener("visibilitychange", onVisibility);
      if (releaseWebLock) {
        releaseWebLock();
        releaseWebLock = null;
      }
      if (wakeSentinel) {
        void wakeSentinel.release().catch(() => {});
        wakeSentinel = null;
      }
      wakeLockActive = false;
      webLockActive = false;
    },
  };
}

/**
 * 断网自动暂停并持续探测恢复（默认最长静候 30 分钟，每 5 秒检查一次 `/api/health`）：
 * - 只要网络恢复且服务可达立即返回 true，外层无缝续跑当前切片；
 * - 若期间用户点击「停止任务」，通过 AbortSignal 立即抛出 TaskAbortedError。
 */
export async function waitForNetworkRecovery(params: {
  probeHealth: () => Promise<boolean>;
  onTick?: (elapsedSec: number) => void;
  signal?: AbortSignal;
  maxWaitMs?: number;
}): Promise<boolean> {
  const maxWaitMs = params.maxWaitMs ?? 30 * 60 * 1000;
  const startedAt = Date.now();

  while (Date.now() - startedAt < maxWaitMs) {
    if (params.signal?.aborted) {
      throw new TaskAbortedError();
    }
    const elapsedSec = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
    params.onTick?.(elapsedSec);

    if (typeof navigator === "undefined" || navigator.onLine !== false) {
      try {
        const ok = await params.probeHealth();
        if (ok) return true;
      } catch {
        // 仍未连通，继续静候
      }
    }

    // 监听浏览器 online 事件提前唤醒，否则每 5 秒轮询一次
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const done = (fn: () => void) => {
        if (settled) return;
        settled = true;
        window.removeEventListener("online", onOnline);
        fn();
      };
      const onOnline = () => done(resolve);
      window.addEventListener("online", onOnline, { once: true });
      workerSleep(5000, params.signal)
        .then(() => done(resolve))
        .catch((err) => done(() => reject(err)));
    });
  }

  return false;
}

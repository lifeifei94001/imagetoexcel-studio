/**
 * 超长报价单智能找缝切图 + 顶部列头视觉锚定：
 * 1. 常规尺寸图片（height <= 1500 或高宽比 < 1.35）保宽原像素直传；
 * 2. 超长报价单（最高支持 10:1+，动态切分 2~14 段，每段约 1000px 保持高清）：
 *    - 在 Canvas 上扫描目标切点附近的像素行，优先寻找「贯穿左侧型号列与表体的完整深色水平边框线」下刀，
 *      彻底避免从跨多行合并的机型单元格（如 13 PRO 的 4 行）中间拦腰切断；
 *    - 若表格无明显横线边框，自动回退到带安全重叠区的切片策略；
 * 3. 自动截取原图表格主列头行，贴附到第 2..N 段切片顶部（红线分隔提示模型仅作列对齐参照），
 *    杜绝中后段列错位或漏掉最右侧扣费/备注列。
 */

export interface SmartSlicePlan {
  files: File[];
  seamMode: "single" | "smart_seam" | "overlap";
  hasHeaderAnchor: boolean;
}

export async function prepareImageForUpload(file: File, maxBytes = 3.6 * 1024 * 1024): Promise<File> {
  if (file.size <= maxBytes) {
    return file;
  }
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".heic") || lower.endsWith(".heif")) {
    return file;
  }

  return new Promise((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const maxWidth = 1800;
      const maxHeight = 14000;
      let { width, height } = img;
      const ratio = Math.min(1, maxWidth / width, maxHeight / height);
      width = Math.round(width * ratio);
      height = Math.round(height * ratio);

      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        resolve(file);
        return;
      }
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(img, 0, 0, width, height);

      const tryEncode = (quality: number) => {
        canvas.toBlob(
          (blob) => {
            if (!blob) {
              resolve(file);
              return;
            }
            if (blob.size > maxBytes && quality > 0.8) {
              tryEncode(0.8);
              return;
            }
            if (blob.size >= file.size) {
              resolve(file);
              return;
            }
            const outName = file.name.replace(/\.[^.]+$/, "") + ".jpg";
            resolve(new File([blob], outName, { type: "image/jpeg" }));
          },
          "image/jpeg",
          quality
        );
      };
      tryEncode(0.92);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(file);
    };
    img.src = url;
  });
}

interface RowScanMetrics {
  leftDarkRatio: Float32Array;
  bodyDarkRatio: Float32Array;
  bodyWhiteRatio: Float32Array;
  bodyVariance: Float32Array;
}

/** 对整张长图做低成本灰度行扫描，用于检测跨左侧型号列的完整水平分隔线与表头位置。 */
function scanImageRows(img: HTMLImageElement, targetW: number, targetH: number): RowScanMetrics | null {
  const scanW = Math.min(480, Math.max(160, Math.round(targetW / 3)));
  const scanScale = scanW / targetW;
  const scanH = Math.max(1, Math.round(targetH * scanScale));

  const canvas = document.createElement("canvas");
  canvas.width = scanW;
  canvas.height = scanH;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, scanW, scanH);
  ctx.drawImage(img, 0, 0, img.width, img.height, 0, 0, scanW, scanH);

  let data: Uint8ClampedArray;
  try {
    data = ctx.getImageData(0, 0, scanW, scanH).data;
  } catch {
    return null;
  }

  const leftDarkRatio = new Float32Array(scanH);
  const bodyDarkRatio = new Float32Array(scanH);
  const bodyWhiteRatio = new Float32Array(scanH);
  const bodyVariance = new Float32Array(scanH);

  const leftStart = Math.floor(scanW * 0.02);
  const leftEnd = Math.max(leftStart + 4, Math.floor(scanW * 0.16));
  const bodyStart = leftEnd;
  const bodyEnd = Math.max(bodyStart + 8, Math.floor(scanW * 0.82));
  const leftCount = leftEnd - leftStart;
  const bodyCount = bodyEnd - bodyStart;

  for (let y = 0; y < scanH; y++) {
    const rowBase = y * scanW * 4;
    let lDark = 0;
    for (let x = leftStart; x < leftEnd; x++) {
      const idx = rowBase + x * 4;
      const lum = 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
      if (lum < 155) lDark++;
    }
    leftDarkRatio[y] = lDark / leftCount;

    let bDark = 0;
    let bWhite = 0;
    let sumLum = 0;
    let sumSq = 0;
    for (let x = bodyStart; x < bodyEnd; x++) {
      const idx = rowBase + x * 4;
      const lum = 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
      if (lum < 155) bDark++;
      if (lum > 235) bWhite++;
      sumLum += lum;
      sumSq += lum * lum;
    }
    const mean = sumLum / bodyCount;
    bodyDarkRatio[y] = bDark / bodyCount;
    bodyWhiteRatio[y] = bWhite / bodyCount;
    bodyVariance[y] = Math.max(0, sumSq / bodyCount - mean * mean);
  }

  return { leftDarkRatio, bodyDarkRatio, bodyWhiteRatio, bodyVariance };
}

/** 侦测表格顶部列头条的纵坐标范围 [topY, bottomY]（目标分辨率坐标系）。 */
function detectHeaderStripRange(
  metrics: RowScanMetrics | null,
  targetH: number
): { topY: number; bottomY: number } | null {
  if (!metrics) return null;
  const scanH = metrics.bodyWhiteRatio.length;
  const scaleBack = targetH / scanH;
  const maxScanY = Math.min(scanH - 10, Math.round((Math.min(1400, targetH * 0.26)) / scaleBack));

  // 找到第一个连续多行出现白底数据区的起始位置（即顶部表头正下方）
  let firstDataScanY = -1;
  let consecutiveWhite = 0;
  for (let y = Math.round(scanH * 0.015); y < maxScanY; y++) {
    if (metrics.bodyWhiteRatio[y] >= 0.45 && metrics.leftDarkRatio[y] < 0.5) {
      consecutiveWhite++;
      if (consecutiveWhite >= Math.max(4, Math.round(14 / scaleBack))) {
        firstDataScanY = y - consecutiveWhite + 1;
        break;
      }
    } else {
      consecutiveWhite = 0;
    }
  }

  if (firstDataScanY <= 0) return null;
  const bottomY = Math.round(firstDataScanY * scaleBack);
  // 向上截取 95~135px 覆盖完整列头行（如 型号/网络型号/容量/靓机/小花/大花/外爆/内爆可测/扣费说明）
  const stripH = Math.min(130, Math.max(84, Math.round(bottomY * 0.42)));
  const topY = Math.max(0, bottomY - stripH);
  if (bottomY - topY < 48 || bottomY > targetH * 0.28) {
    return null;
  }
  return { topY, bottomY };
}

/** 在理想切点附近寻找贯穿左侧型号列与表体的完整横线（机型区块分界线）。 */
function findBestModelBoundaryCut(
  metrics: RowScanMetrics | null,
  targetH: number,
  idealY: number,
  minY: number,
  maxY: number
): { cutY: number; isCleanSeam: boolean } {
  if (!metrics) {
    return { cutY: idealY, isCleanSeam: false };
  }
  const scanH = metrics.leftDarkRatio.length;
  const scaleBack = targetH / scanH;
  const lo = Math.max(2, Math.floor(minY / scaleBack));
  const hi = Math.min(scanH - 3, Math.ceil(maxY / scaleBack));
  const idealScan = idealY / scaleBack;

  let bestScore = -1;
  let bestScanY = -1;

  for (let y = lo; y <= hi; y++) {
    // 兼顾 1~2px 缩放抗锯齿，取邻近 3 行最高横线强度
    const lDark = Math.max(metrics.leftDarkRatio[y - 1], metrics.leftDarkRatio[y], metrics.leftDarkRatio[y + 1]);
    const bDark = Math.max(metrics.bodyDarkRatio[y - 1], metrics.bodyDarkRatio[y], metrics.bodyDarkRatio[y + 1]);

    // 必须同时贯穿左侧型号列与表体，才是真正位于两个机型大块之间的水平分割线！
    if (lDark >= 0.48 && bDark >= 0.48) {
      const distPenalty = Math.abs(y - idealScan) / Math.max(1, hi - lo);
      const score = lDark * 1.15 + bDark - distPenalty * 0.45;
      if (score > bestScore) {
        bestScore = score;
        bestScanY = y;
      }
    }
  }

  if (bestScanY !== -1) {
    return { cutY: Math.round(bestScanY * scaleBack), isCleanSeam: true };
  }

  // 次优兜底：寻找近乎纯色无文字的水平空白缝（适用于无边框表格）
  let bestGapScore = -1;
  let bestGapY = -1;
  for (let y = lo; y <= hi; y++) {
    if (metrics.bodyVariance[y] < 18 && metrics.leftDarkRatio[y] < 0.08 && metrics.bodyDarkRatio[y] < 0.08) {
      const distPenalty = Math.abs(y - idealScan) / Math.max(1, hi - lo);
      const score = 1.0 - distPenalty * 0.5;
      if (score > bestGapScore) {
        bestGapScore = score;
        bestGapY = y;
      }
    }
  }
  if (bestGapY !== -1) {
    return { cutY: Math.round(bestGapY * scaleBack), isCleanSeam: false };
  }

  return { cutY: idealY, isCleanSeam: false };
}

export async function splitLongImageSmart(file: File): Promise<SmartSlicePlan> {
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".heic") || lower.endsWith(".heif")) {
    return { files: [file], seamMode: "single", hasHeaderAnchor: false };
  }

  return new Promise((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = async () => {
      URL.revokeObjectURL(url);
      const origW = img.width;
      const origH = img.height;

      // 常规尺寸图片无需切片
      if (origH <= 1500 || origH / Math.max(1, origW) < 1.35) {
        resolve({
          files: [await prepareImageForUpload(file)],
          seamMode: "single",
          hasHeaderAnchor: false,
        });
        return;
      }

      // 限制单边尺寸防 Canvas 超限黑屏（10:1 超长图同样保宽清晰）
      const maxCanvasH = 14500;
      const widthScale = Math.min(1, 1800 / origW);
      const heightScale = Math.min(1, maxCanvasH / origH);
      const scale = Math.min(widthScale, heightScale);
      const targetW = Math.max(1, Math.round(origW * scale));
      const targetH = Math.max(1, Math.round(origH * scale));

      const metrics = scanImageRows(img, targetW, targetH);
      const headerRange = detectHeaderStripRange(metrics, targetH);

      const maxSlices = 14;
      const desiredSliceH = Math.max(960, Math.ceil(targetH / maxSlices));
      const fallbackOverlap = 180;

      // 逐段智能寻缝计算每段 [startY, endY]
      const ranges: Array<{ startY: number; endY: number }> = [];
      let curY = 0;
      let cleanSeamHits = 0;

      while (curY < targetH && ranges.length < maxSlices) {
        const remaining = targetH - curY;
        const isLastAllowed = ranges.length === maxSlices - 1;
        if (isLastAllowed || remaining <= desiredSliceH * 1.3) {
          ranges.push({ startY: curY, endY: targetH });
          break;
        }

        const idealY = curY + desiredSliceH;
        const minSearchY = Math.max(curY + 520, idealY - 320);
        const maxSearchY = Math.min(targetH - 260, idealY + 280);
        const { cutY, isCleanSeam } = findBestModelBoundaryCut(metrics, targetH, idealY, minSearchY, maxSearchY);

        if (isCleanSeam) {
          cleanSeamHits++;
          // 在机型边框线上精准下刀，仅留 4px 线条防截字，彻底不让上一个机型的末尾行漏入下一段
          ranges.push({ startY: curY, endY: Math.min(targetH, cutY + 4) });
          curY = Math.max(curY + 320, cutY - 4);
        } else {
          ranges.push({ startY: curY, endY: Math.min(targetH, cutY) });
          curY = Math.max(curY + 320, cutY - fallbackOverlap);
        }
      }

      const slices: File[] = [];
      const stem = file.name.replace(/\.[^.]+$/, "");
      const hasHeaderAnchor = Boolean(headerRange && ranges.length > 1);
      const sepBarH = 6;

      for (let i = 0; i < ranges.length; i++) {
        const { startY, endY } = ranges[i];
        const bodyH = Math.max(1, endY - startY);
        if (bodyH <= 80 && i > 0) continue;

        const attachHeader = i > 0 && hasHeaderAnchor && headerRange !== null;
        const headerH = attachHeader && headerRange ? headerRange.bottomY - headerRange.topY : 0;
        const canvasH = bodyH + (attachHeader ? headerH + sepBarH : 0);

        const canvas = document.createElement("canvas");
        canvas.width = targetW;
        canvas.height = canvasH;
        const ctx = canvas.getContext("2d");
        if (!ctx) continue;

        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, targetW, canvasH);

        let bodyDrawY = 0;
        if (attachHeader && headerRange) {
          const hSrcY = headerRange.topY / scale;
          const hSrcH = headerH / scale;
          ctx.drawImage(img, 0, hSrcY, origW, hSrcH, 0, 0, targetW, headerH);
          // 绘制醒目红色横向分割线，明确区分顶部列头参照条与下方本段真实数据区
          ctx.fillStyle = "#e11d48";
          ctx.fillRect(0, headerH, targetW, sepBarH);
          bodyDrawY = headerH + sepBarH;
        }

        const srcY = startY / scale;
        const srcH = bodyH / scale;
        ctx.drawImage(img, 0, srcY, origW, srcH, 0, bodyDrawY, targetW, bodyH);

        const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, "image/jpeg", 0.92));
        if (blob) {
          slices.push(new File([blob], `${stem}_part${i + 1}.jpg`, { type: "image/jpeg" }));
        }
      }

      if (slices.length === 0) {
        resolve({
          files: [await prepareImageForUpload(file)],
          seamMode: "single",
          hasHeaderAnchor: false,
        });
        return;
      }

      resolve({
        files: slices,
        seamMode: cleanSeamHits > 0 ? "smart_seam" : "overlap",
        hasHeaderAnchor,
      });
    };
    img.onerror = async () => {
      URL.revokeObjectURL(url);
      resolve({
        files: [await prepareImageForUpload(file)],
        seamMode: "single",
        hasHeaderAnchor: false,
      });
    };
    img.src = url;
  });
}

export async function splitLongImageIntoSlices(file: File): Promise<File[]> {
  const plan = await splitLongImageSmart(file);
  return plan.files;
}

/**
 * 超长报价单 1:1 原像素智能找缝切图 + 精确单行表头视觉锚定：
 * 1. 常规尺寸图片（height <= 1400 或高宽比 < 1.35）保宽原像素直传；
 * 2. 超长报价单（最高支持 19:1+ 如 874×16675 的 OPPO/红米/华为报价单）：
 *    - 顶部 1400px 内 1:1 精扫：精确定位「第一个白底数据行 firstDataY」及紧贴其上方的单行绿色列头 [hdrTop..hdrBottom]（通常 38~55px）；
 *    - 第 1 段切片确保涵盖「标题 + 列头 + 首批 ~850px 机型数据」，避免顶部 750px 的成色说明公告独占第 1 段；
 *    - 后续切片按 100% 原像素直切（单段理想高度 ~850px，动态最多 30 段），优先在「贯穿左侧型号列与表体的完整深色横线」处下刀，
 *      彻底杜绝从合并机型单元格中间拦腰切断；
 *    - 第 2..N 段切片顶部自动贴附精确截取的单行原图列头 + 6px 红线分隔条，杜绝列漂移与漏右侧列。
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

interface HeaderBandInfo {
  topY: number;
  bottomY: number;
  firstDataY: number;
}

/** 1:1 扫描图片顶部 1400px，像素级锁定第一个白底数据行与紧贴其上方的单行表格列头 [topY..bottomY]。 */
function detectExactHeaderBand(
  img: HTMLImageElement,
  scale: number,
  targetW: number,
  targetH: number
): HeaderBandInfo | null {
  const maxY = Math.min(targetH - 4, 1400);
  if (maxY < 80) return null;

  const canvas = document.createElement("canvas");
  canvas.width = targetW;
  canvas.height = maxY;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, targetW, maxY);
  ctx.drawImage(img, 0, 0, img.width, maxY / scale, 0, 0, targetW, maxY);

  let data: Uint8ClampedArray;
  try {
    data = ctx.getImageData(0, 0, targetW, maxY).data;
  } catch {
    return null;
  }

  const leftStart = Math.floor(targetW * 0.02);
  const leftEnd = Math.max(leftStart + 4, Math.floor(targetW * 0.14));
  const bodyStart = leftEnd;
  const bodyEnd = Math.max(bodyStart + 8, Math.floor(targetW * 0.82));
  // 白色价格区起点右移至 44%，兼容苹果 iPad 左侧 4 列全绿底布局
  const whiteStart = Math.floor(targetW * 0.44);
  const whiteEnd = Math.max(whiteStart + 8, Math.floor(targetW * 0.82));
  const leftCount = leftEnd - leftStart;
  const bodyCount = bodyEnd - bodyStart;
  const whiteCount = whiteEnd - whiteStart;

  const lDark = new Float32Array(maxY);
  const bDark = new Float32Array(maxY);
  const bWhite = new Float32Array(maxY);
  const rowMeanLum = new Float32Array(maxY);

  for (let y = 0; y < maxY; y++) {
    const rowBase = y * targetW * 4;
    let ld = 0;
    for (let x = leftStart; x < leftEnd; x++) {
      const idx = rowBase + x * 4;
      if (0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2] < 155) ld++;
    }
    lDark[y] = ld / leftCount;

    let bd = 0;
    let lumSum = 0;
    for (let x = bodyStart; x < bodyEnd; x++) {
      const idx = rowBase + x * 4;
      const lum = 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
      if (lum < 155) bd++;
      lumSum += lum;
    }
    bDark[y] = bd / bodyCount;
    rowMeanLum[y] = lumSum / bodyCount;

    let bw = 0;
    for (let x = whiteStart; x < whiteEnd; x++) {
      const idx = rowBase + x * 4;
      if (0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2] > 238) bw++;
    }
    bWhite[y] = bw / whiteCount;
  }

  // 找到第一个连续 18px 出现白底价格数据区的起始位置（绕开顶部纯绿/黄公告与成色说明）
  let consec = 0;
  let firstDataY = -1;
  for (let y = 36; y < maxY; y++) {
    if (bWhite[y] >= 0.55 && lDark[y] < 0.45) {
      consec++;
      if (consec >= 18) {
        firstDataY = y - consec + 1;
        break;
      }
    } else {
      consec = 0;
    }
  }

  if (firstDataY > 24) {
    // 路径 A：有黑色横线的标准报价单 / 网格表，精准截取单行绿色/浅色列头
    let hdrBottom = -1;
    for (let y = firstDataY; y >= Math.max(0, firstDataY - 24); y--) {
      if (lDark[y] >= 0.45 && bDark[y] >= 0.45) {
        hdrBottom = y;
        break;
      }
    }
    if (hdrBottom > 24) {
      let hdrTop = Math.max(0, hdrBottom - 64);
      for (let y = hdrBottom - 24; y >= Math.max(0, hdrBottom - 115); y--) {
        if (lDark[y] >= 0.45 && bDark[y] >= 0.45) {
          hdrTop = y;
          break;
        }
      }
      const stripH = hdrBottom - hdrTop;
      if (stripH >= 20 && stripH <= 130) {
        return { topY: hdrTop, bottomY: hdrBottom + 1, firstDataY };
      }
    }

    // 路径 B（通用无边框现代长表兜底，如 ERP/飞书/Notion 蓝灰底色表头 + 白底数据区）：
    // 在 firstDataY 上方检测连续 24~84px 的深色/彩底色表头带（平均亮度 < 215）
    let bandBottom = firstDataY - 1;
    while (bandBottom > 0 && rowMeanLum[bandBottom] >= 225) {
      bandBottom--;
    }
    let bandTop = bandBottom;
    while (bandTop > 0 && rowMeanLum[bandTop] < 218 && bandBottom - bandTop < 90) {
      bandTop--;
    }
    const bandH = bandBottom - bandTop;
    if (bandH >= 24 && bandH <= 88) {
      return { topY: Math.max(0, bandTop), bottomY: bandBottom + 1, firstDataY };
    }
  }

  return null;
}

/** 对整张超长图做轻量灰度行扫描（兼顾 1px 细实线清晰度且不触碰浏览器 Canvas 尺寸上限）。 */
function scanImageRows(img: HTMLImageElement, targetW: number, targetH: number): RowScanMetrics | null {
  const widthScanScale = Math.min(1, 560 / Math.max(1, targetW));
  const heightScanScale = Math.min(1, 10000 / Math.max(1, targetH));
  const scanScale = Math.min(widthScanScale, heightScanScale);
  const scanW = Math.max(160, Math.round(targetW * scanScale));
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
  const leftEnd = Math.max(leftStart + 4, Math.floor(scanW * 0.14));
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
  const lo = Math.max(4, Math.floor(minY / scaleBack));
  const hi = Math.min(scanH - 5, Math.ceil(maxY / scaleBack));
  const idealScan = idealY / scaleBack;

  let bestScore = -1;
  let bestScanY = -1;

  for (let y = lo; y <= hi; y++) {
    const lDark = metrics.leftDarkRatio[y];
    const bDark = metrics.bodyDarkRatio[y];

    // 必须为贯穿左侧型号列与表体的高覆盖实线（>= 0.76），且上下 3~4px 并非大片粗字体像素，
    // 彻底杜绝把「15 PRO」等居大型号文字像素误判成横线而拦腰切开合并单元格！
    if (lDark >= 0.76 && bDark >= 0.72) {
      const aboveLeft = (metrics.leftDarkRatio[y - 3] + metrics.leftDarkRatio[y - 4]) * 0.5;
      const belowLeft = (metrics.leftDarkRatio[y + 3] + metrics.leftDarkRatio[y + 4]) * 0.5;
      if (aboveLeft < 0.35 && belowLeft < 0.35) {
        const distPenalty = Math.abs(y - idealScan) / Math.max(1, hi - lo);
        const score = lDark * 1.2 + bDark - distPenalty * 0.45;
        if (score > bestScore) {
          bestScore = score;
          bestScanY = y;
        }
      }
    }
  }

  if (bestScanY !== -1) {
    return { cutY: Math.round(bestScanY * scaleBack), isCleanSeam: true };
  }

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
      if (origH <= 1400 || origH / Math.max(1, origW) < 1.35) {
        resolve({
          files: [await prepareImageForUpload(file)],
          seamMode: "single",
          hasHeaderAnchor: false,
        });
        return;
      }

      // 1:1 原像素直切：仅在宽度超过 1800px 时按宽限幅，绝不因超长（如 16675px）而纵向压糊！
      const scale = Math.min(1, 1800 / origW);
      const targetW = Math.max(1, Math.round(origW * scale));
      const targetH = Math.max(1, Math.round(origH * scale));

      const headerBand = detectExactHeaderBand(img, scale, targetW, targetH);
      const metrics = scanImageRows(img, targetW, targetH);

      const maxSlices = 30;
      const dataStartY = headerBand ? headerBand.firstDataY : 0;
      const dataTotalH = Math.max(1, targetH - dataStartY);
      const desiredSliceH = Math.max(850, Math.ceil(dataTotalH / maxSlices));
      const fallbackOverlap = 160;

      const ranges: Array<{ startY: number; endY: number }> = [];
      let curY = 0;
      let cleanSeamHits = 0;

      while (curY < targetH && ranges.length < maxSlices) {
        const isFirst = ranges.length === 0;
        // 第 1 段确保覆盖顶部标题/公告 + 首批 desiredSliceH 真实机型数据，杜绝第 1 段只含成色说明！
        const effectiveStart = isFirst ? dataStartY : curY;
        const remainingData = targetH - effectiveStart;
        const isLastAllowed = ranges.length === maxSlices - 1;

        if (isLastAllowed || remainingData <= desiredSliceH * 1.28) {
          ranges.push({ startY: curY, endY: targetH });
          break;
        }

        const idealY = effectiveStart + desiredSliceH;
        const minSearchY = Math.max(effectiveStart + 420, idealY - 280);
        const maxSearchY = Math.min(targetH - 220, idealY + 260);
        const { cutY, isCleanSeam } = findBestModelBoundaryCut(metrics, targetH, idealY, minSearchY, maxSearchY);

        if (isCleanSeam) {
          cleanSeamHits++;
          ranges.push({ startY: curY, endY: Math.min(targetH, cutY + 3) });
          curY = Math.max(effectiveStart + 280, cutY - 3);
        } else {
          ranges.push({ startY: curY, endY: Math.min(targetH, cutY) });
          curY = Math.max(effectiveStart + 280, cutY - fallbackOverlap);
        }
      }

      const slices: File[] = [];
      const stem = file.name.replace(/\.[^.]+$/, "");
      const hasHeaderAnchor = Boolean(headerBand && ranges.length > 1);
      const sepBarH = 6;

      for (let i = 0; i < ranges.length; i++) {
        const { startY, endY } = ranges[i];
        const bodyH = Math.max(1, endY - startY);
        if (bodyH <= 80 && i > 0) continue;

        const attachHeader = i > 0 && hasHeaderAnchor && headerBand !== null;
        const headerH = attachHeader && headerBand ? headerBand.bottomY - headerBand.topY : 0;
        const canvasH = bodyH + (attachHeader ? headerH + sepBarH : 0);

        const canvas = document.createElement("canvas");
        canvas.width = targetW;
        canvas.height = canvasH;
        const ctx = canvas.getContext("2d");
        if (!ctx) continue;

        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, targetW, canvasH);

        let bodyDrawY = 0;
        if (attachHeader && headerBand) {
          const hSrcY = headerBand.topY / scale;
          const hSrcH = headerH / scale;
          ctx.drawImage(img, 0, hSrcY, origW, hSrcH, 0, 0, targetW, headerH);
          ctx.fillStyle = "#e11d48";
          ctx.fillRect(0, headerH, targetW, sepBarH);
          bodyDrawY = headerH + sepBarH;
        }

        const srcY = startY / scale;
        const srcH = bodyH / scale;
        ctx.drawImage(img, 0, srcY, origW, srcH, 0, bodyDrawY, targetW, bodyH);

        const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, "image/jpeg", 0.96));
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

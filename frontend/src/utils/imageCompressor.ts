/**
 * 长图保真上传与自动重叠切片：
 * 1. <= 3.6MB 的常规图片 100% 原像素直传；超过 3.6MB 保宽不截长 (maxWidth=1800, maxHeight=7200)。
 * 2. 竖向超长报价单（height > 1700 且高宽比 >= 1.35）自动切分为带重叠的 2~4 段横向切片，
 *    既彻底杜绝 ReadTimeout，又避免大模型一次输出 100+ 行时截断漏行。
 */
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
      const maxHeight = 7200;
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

export async function splitLongImageIntoSlices(file: File): Promise<File[]> {
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".heic") || lower.endsWith(".heif")) {
    return [file];
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
        resolve([await prepareImageForUpload(file)]);
        return;
      }

      const targetW = Math.min(origW, 1800);
      const scale = targetW / origW;
      const targetH = Math.round(origH * scale);

      const maxSlices = 6;
      const overlap = 220;
      const step = Math.max(860, Math.ceil((targetH - overlap) / maxSlices));
      const sliceCount = Math.min(maxSlices, Math.max(2, Math.ceil((targetH - overlap) / step)));
      const sliceHeight = Math.ceil((targetH + overlap * (sliceCount - 1)) / sliceCount);

      const slices: File[] = [];
      for (let i = 0; i < sliceCount; i++) {
        const destY = Math.max(0, i * (sliceHeight - overlap));
        const actualH = Math.min(sliceHeight, targetH - destY);
        if (actualH <= 80) break;

        const srcY = destY / scale;
        const srcH = actualH / scale;

        const canvas = document.createElement("canvas");
        canvas.width = targetW;
        canvas.height = actualH;
        const ctx = canvas.getContext("2d");
        if (!ctx) continue;

        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, targetW, actualH);
        ctx.drawImage(img, 0, srcY, origW, srcH, 0, 0, targetW, actualH);

        const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, "image/jpeg", 0.92));
        if (blob) {
          const stem = file.name.replace(/\.[^.]+$/, "");
          slices.push(new File([blob], `${stem}_part${i + 1}.jpg`, { type: "image/jpeg" }));
        }
      }

      resolve(slices.length > 0 ? slices : [await prepareImageForUpload(file)]);
    };
    img.onerror = async () => {
      URL.revokeObjectURL(url);
      resolve([await prepareImageForUpload(file)]);
    };
    img.src = url;
  });
}

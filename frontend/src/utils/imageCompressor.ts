/**
 * 上传前智能缩放压缩（R3 避坑：规避 Vercel Serverless 4.5MB 请求体上限，同时保证表格文字锐利可读）
 */
export async function prepareImageForUpload(file: File, maxBytes = 3.2 * 1024 * 1024): Promise<File> {
  if (file.size <= maxBytes) {
    return file;
  }
  const lower = file.name.toLowerCase();
  // HEIC 等浏览器原生 canvas 不一定能解码的格式直接原样透传（后端会校验 <=10MB）
  if (lower.endsWith(".heic") || lower.endsWith(".heif")) {
    return file;
  }

  return new Promise((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const maxDim = 2400;
      let { width, height } = img;
      if (width > maxDim || height > maxDim) {
        const ratio = Math.min(maxDim / width, maxDim / height);
        width = Math.round(width * ratio);
        height = Math.round(height * ratio);
      }

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

      canvas.toBlob(
        (blob) => {
          if (!blob || blob.size >= file.size) {
            resolve(file);
            return;
          }
          const outName = file.name.replace(/\.[^.]+$/, "") + ".jpg";
          resolve(new File([blob], outName, { type: "image/jpeg" }));
        },
        "image/jpeg",
        0.9
      );
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(file);
    };
    img.src = url;
  });
}

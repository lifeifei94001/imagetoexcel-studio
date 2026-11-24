/**
 * 长图保真上传预处理：
 * - 单图请求独享 Vercel 4.5MB 载荷上限，<= 3.6MB 原图 100% 原像素直传不压缩；
 * - 超过 3.6MB 的超长报价截图采用「保宽不截长」(maxWidth=1800, maxHeight=7200)，避免压糊价格数字。
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

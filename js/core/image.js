/* Chuẩn bị ảnh đại diện ngay trên trình duyệt: kiểm tra loại/dung lượng, cắt vuông ở giữa, thu về 256×256, xuất JPEG nhẹ. */
export const AVATAR_TYPES = ["image/jpeg", "image/png", "image/webp"];
export const AVATAR_MAX_BYTES = 5 * 1024 * 1024;
const SIZE = 256;
const fail = code => Object.assign(new Error(code), { code });

export async function prepareAvatar(file) {
  if (!AVATAR_TYPES.includes(file.type)) throw fail("type");
  if (file.size > AVATAR_MAX_BYTES) throw fail("size");
  const bitmap = await createImageBitmap(file).catch(() => { throw fail("read"); });
  const side = Math.min(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = SIZE;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff";   // nền trắng cho ảnh PNG/WebP trong suốt khi chuyển sang JPEG
  ctx.fillRect(0, 0, SIZE, SIZE);
  ctx.drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, SIZE, SIZE);
  bitmap.close?.();
  return new Promise((resolve, reject) => canvas.toBlob(b => (b ? resolve(b) : reject(fail("read"))), "image/jpeg", 0.88));
}

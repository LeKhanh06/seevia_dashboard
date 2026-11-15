/* Thao tác cá nhân của chính quản trị viên đang đăng nhập (không đụng tới dữ liệu hệ thống). */
import { api } from "./client.js";

export const changePassword = (currentPassword, newPassword) =>
  api.post("/admin/auth/password", { current_password: currentPassword, new_password: newPassword }, { retry: false });

// blob: ảnh đã cắt vuông và thu nhỏ ở trình duyệt; gửi multipart, trường "file".
export function uploadAvatar(blob) {
  const form = new FormData();
  form.append("file", blob, "avatar.jpg");
  return api.put("/admin/auth/avatar", form);
}
export const removeAvatar = () => api.delete("/admin/auth/avatar");

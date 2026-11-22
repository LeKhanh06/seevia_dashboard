/*
 * Cấu hình duy nhất của frontend. Ghép backend thật chỉ cần:
 *   USE_MOCK = false  và  API_BASE_URL = "<địa chỉ API>"
 * Không có địa chỉ máy chủ nào được viết cứng ở nơi khác trong mã.
 */
export const CONFIG = Object.freeze({
  // Gốc của API; các đường dẫn bắt đầu bằng /admin/... được nối vào sau gốc này.
  // Có thể là đường dẫn tương đối (cùng origin) hoặc URL đầy đủ (backend khác origin, cần CORS).
  API_BASE_URL: "/api",

  // true: dữ liệu mẫu trong js/api/mock/ (cùng hình dạng với API thật). false: gọi API thật.
  USE_MOCK: true,

  // Thời gian chờ tối đa cho mỗi yêu cầu (mili giây).
  REQUEST_TIMEOUT_MS: 15000,

  // Múi giờ tính "hôm nay / tuần này / tháng này" và hiển thị ngày giờ.
  TIMEZONE: "Asia/Ho_Chi_Minh",
  LOCALE: "vi-VN",

  // true: Xuất CSV gọi chính endpoint với format=csv (backend phải hỗ trợ).
  // false: dashboard tự tạo CSV từ dữ liệu đang hiển thị (UTF-8 có BOM).
  CSV_SERVER_EXPORT: false,

  // Định dạng file khi dashboard tự tạo từ dữ liệu đang hiển thị: "xlsx" (mở đúng tiếng Việt ở mọi phần mềm bảng tính,
  // khuyến nghị) hoặc "csv" (UTF-8 có BOM; một số phần mềm như WPS không nhận BOM nên có thể lỗi font).
  EXPORT_FORMAT: "xlsx",

  // Số dòng mỗi trang cho các danh sách phân trang phía server.
  PAGE_SIZE: 10,

  // Tự cập nhật trang thống kê (giây): tải lại ngầm khi kỳ đang xem có hôm nay và tab đang mở; 0 = tắt.
  // Số liệu ở server trễ ~10–15 giây (VH gửi sự kiện mỗi 5 giây, AB gộp mỗi 5 giây).
  AUTO_REFRESH_S: 20,

  // Chỉ dùng khi USE_MOCK = true: độ trễ giả lập (mili giây).
  MOCK_LATENCY_MS: 220,
});

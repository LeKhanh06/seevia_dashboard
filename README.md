# SeeVia Admin Dashboard

Bảng điều khiển dành cho nhóm vận hành **SeeVia** (kính thông minh cho người khiếm thị). Dashboard **chỉ xem dữ liệu hệ thống**: số liệu tổng hợp toàn hệ thống, danh sách tài khoản, danh sách thiết bị và lỗi hệ thống. Không có thao tác tạo, sửa, xóa dữ liệu và không hiển thị số liệu của từng cá nhân. Ngoại lệ duy nhất: quản trị viên tự **đổi mật khẩu** và **đổi ảnh đại diện** của chính mình ở trang Hồ sơ.

Ứng dụng là frontend tĩnh (HTML, CSS, JavaScript ES module), **không cần cài đặt hay build**, chạy được ngay với dữ liệu mẫu và ghép backend thật chỉ bằng cách đổi hai dòng cấu hình.

## Tính năng

- **Tổng quan**: lượt sử dụng, chủ kính hoạt động, tỷ lệ hoàn thành, thời gian sử dụng; so sánh với kỳ trước; biểu đồ theo ngày/giờ.
- **Báo cáo chức năng**: Đọc & Nhận diện, Tìm & Hiểu, Kết nối, Di chuyển, Cộng đồng & Thiết bị.
- **Phân tích**: Thời điểm (hôm nay theo giờ; tuần theo ngày rồi giờ × thứ; tháng theo tuần, theo ngày rồi giờ × thứ), Chất lượng & tốc độ, Gắn bó (hoạt động theo kỳ chọn: DAU hôm nay, WAU tuần này, MAU tháng này), An toàn (SOS, cảnh báo vật cản).
- **Quản trị (chỉ xem)**: Tài khoản, Thiết bị, Lỗi hệ thống, Cấu hình.
- **Hồ sơ quản trị viên**: thông tin tài khoản, đổi ảnh đại diện (tự cắt vuông, thu nhỏ trên trình duyệt), đổi mật khẩu (kiểm tra mật khẩu hiện tại, độ mạnh, nhập lại).
- Chọn kỳ **hôm nay / tuần này / tháng này** (giờ Việt Nam); kỳ nằm trong URL nên chia sẻ link vẫn đúng.
- **Xuất Excel (.xlsx)** từ dữ liệu đang hiển thị: tiếng Việt đúng ở Excel, WPS, LibreOffice; có thể đổi sang CSV trong cấu hình.
- Giao diện **Sáng / Tối**, responsive từ điện thoại đến màn hình rộng, hỗ trợ bàn phím và trình đọc màn hình.
- Trạng thái **đang tải, rỗng, lỗi** cho mọi trang; tự thử lại khi lỗi mạng.

## Chạy thử

Cần một máy chủ tĩnh bất kỳ (ES module không chạy qua `file://`):

```bash
python -m http.server 8765
```

Chạy lệnh trong thư mục chứa `index.html`, rồi mở `http://127.0.0.1:8765/`. Có thể dùng Live Server (VS Code) hoặc `npx serve` thay cho Python.

Ở chế độ dữ liệu mẫu, tài khoản đăng nhập hiển thị ngay trên màn hình đăng nhập. Dữ liệu mẫu mô phỏng quy mô lớn (khoảng 3.000 lượt/tuần, 72 tài khoản, 66 kính), chỉ có số liệu cho ngày giờ đã diễn ra; mật khẩu và ảnh đại diện đã đổi được lưu trong `localStorage` của trình duyệt.

## Kết nối backend thật

Chỉ sửa `js/config.js`, không phải đổi giao diện:

```js
USE_MOCK: false,
API_BASE_URL: "https://api.example.com",   // gốc API; cần bật CORS nếu khác origin
```

| Tùy chọn | Ý nghĩa |
|---|---|
| `USE_MOCK` | `true`: dùng dữ liệu mẫu trong `js/api/mock/`. `false`: gọi API thật |
| `API_BASE_URL` | Gốc API (đường dẫn tương đối hoặc URL đầy đủ) |
| `REQUEST_TIMEOUT_MS` | Thời gian chờ mỗi yêu cầu |
| `TIMEZONE` | Múi giờ tính kỳ báo cáo (`Asia/Ho_Chi_Minh`) |
| `EXPORT_FORMAT` | `"xlsx"` (mặc định) hoặc `"csv"`: định dạng file khi dashboard tự tạo từ dữ liệu đang hiển thị |
| `CSV_SERVER_EXPORT` | `true`: tải CSV do backend trả về (`format=csv`) thay vì tự tạo |
| `PAGE_SIZE` | Số dòng mỗi trang của danh sách |

Backend làm theo **[docs/API_CONTRACT.md](docs/API_CONTRACT.md)**: các endpoint `GET /admin/...` chỉ đọc, `POST /admin/auth/login` và `logout`, cùng ba thao tác hồ sơ cá nhân (`POST /admin/auth/password`, `PUT` và `DELETE /admin/auth/avatar`). Định nghĩa từng chỉ số (tên hiển thị, trường API, đơn vị, cách tính) nằm trong **[docs/METRICS.md](docs/METRICS.md)**.

Mọi tài nguyên dùng đường dẫn tương đối, nên có thể đặt dưới một đường dẫn con (ví dụ `https://example.com/admin/`) mà không hỏng.

## Kiến trúc

Ba lớp tách bạch, view không biết dữ liệu đến từ đâu:

| Lớp | Thư mục | Vai trò |
|---|---|---|
| Dữ liệu | `js/api/` | `client.js` là nơi **duy nhất** gọi mạng: gắn token, timeout, xử lý 401/403/5xx, thử lại GET một lần, hủy yêu cầu cũ khi đổi trang hoặc đổi kỳ. `mock/` chứa dữ liệu mẫu cùng hình dạng với API thật |
| Chuyển đổi | `js/mappers/` | Đưa phản hồi API về cấu trúc hiển thị: định dạng vi-VN, giá trị thiếu hoặc 0 hiện "—", gắn nhãn và tooltip |
| Giao diện | `js/views/` | Chỉ nhận dữ liệu đã chuẩn hóa và dựng DOM; không dùng `innerHTML` với dữ liệu từ API |

```
├── index.html
├── css/
│   ├── tokens.css      # Toàn bộ màu (Sáng/Tối), nơi duy nhất định nghĩa màu
│   ├── app.css         # Bố cục và thành phần
│   ├── motion.css      # Hiệu ứng; chỉnh ở --dur-*, --ease; tự tắt khi bật giảm chuyển động
│   └── fonts.css       # Be Vietnam Pro (lưu sẵn, không dùng CDN)
├── js/
│   ├── config.js       # Cấu hình duy nhất
│   ├── registry.js     # Route → endpoint → mapper → view
│   ├── api/            # Lớp dữ liệu (client.js, profile.js, mock/)
│   ├── mappers/        # Lớp chuyển đổi
│   ├── views/          # Lớp giao diện (thành phần, biểu đồ, khung, các trang)
│   ├── i18n/vi.js      # Toàn bộ chữ hiển thị và tooltip "Cách tính / Tác dụng"
│   ├── core/           # Router hash, kỳ báo cáo, phiên, theme, xuất Excel/CSV, ảnh đại diện, tooltip, motion
│   └── lib/            # DOM, định dạng vi-VN, icon SVG
├── docs/               # API_CONTRACT.md, METRICS.md
├── tools/              # check-routes.mjs (kiểm thử nhanh)
└── assets/             # Logo, font
```

Đường dẫn trang theo hash: `#overview?period=week`, `#accounts?q=tk&status=active&sort=created_at&order=desc&page=2`, `#account-detail/TK-0001`, `#profile`.

## Tùy biến

- **Đổi nhãn, chú thích, tooltip**: sửa `js/i18n/vi.js`, không cần đụng logic.
- **Đổi màu**: sửa `css/tokens.css` (`:root` cho Sáng, `[data-theme="dark"]` cho Tối). Mọi CSS và JS khác chỉ dùng biến, kể cả màu biểu đồ.
- **Thêm hoặc bớt trang**: khai báo trong `js/registry.js` (endpoint, mapper, view).

## Kiểm thử

```bash
node tools/check-routes.mjs
```

Script mở từng trang bằng Chrome hoặc Edge headless (đặt biến `CHROME` nếu trình duyệt không ở vị trí mặc định) và báo lỗi nếu chữ hiển thị, tooltip hay `aria-label` chứa `[object`, `undefined`, `NaN`. Đồng thời chạy kiểm tra chéo dữ liệu mẫu (`js/api/mock/validate.js`): tổng lượt, theo nhóm, theo ngày, heatmap, SOS, trạng thái, số lỗi và phần trăm phải khớp nhau. Kiểm tra này cũng tự chạy khi `USE_MOCK = true` và báo lỗi ra console.

## Bảo mật và quyền riêng tư

- Admin Web chỉ đọc số liệu tổng hợp; email luôn do backend **che sẵn** (ví dụ `ng***@gmail.com`). Thao tác ghi duy nhất là hồ sơ cá nhân của quản trị viên (đổi mật khẩu, ảnh đại diện); mật khẩu chỉ gửi qua HTTPS tới backend, không lưu ở trình duyệt, không ghi console.
- Phiên đăng nhập lưu trong bộ nhớ và `sessionStorage`, mật khẩu không bao giờ được lưu; hết hạn hoặc nhận 401 thì về `#login`.
- Không có khóa bí mật trong mã nguồn; địa chỉ máy chủ chỉ đọc từ `js/config.js`.

## Công nghệ

HTML, CSS, JavaScript ES module thuần (không framework, không bước build). Biểu đồ là SVG tự vẽ. Font Be Vietnam Pro.

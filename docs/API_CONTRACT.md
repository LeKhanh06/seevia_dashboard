# Hợp đồng API — SeeVia Admin Dashboard

Dashboard **chỉ đọc dữ liệu hệ thống**; quản trị viên chỉ tự cập nhật hồ sơ của chính mình (đổi mật khẩu, đổi ảnh đại diện). Backend chỉ cần trả JSON đúng hợp đồng này; frontend lo toàn bộ định dạng hiển thị (locale `vi-VN`).
Chuyển sang backend thật: `js/config.js` → `USE_MOCK = false`, `API_BASE_URL = "<gốc API>"`.

## Quy ước chung

- Mọi đường dẫn bắt đầu bằng `/admin`; mọi endpoint thống kê, danh sách và chi tiết là `GET`, không ghi dữ liệu. Chỉ có ba thao tác ghi, đều là hồ sơ cá nhân của quản trị viên đang đăng nhập: đổi mật khẩu, tải và xóa ảnh đại diện (mục "Xác thực").
- Xác thực: header `Authorization: Bearer <token>` (trừ `POST /admin/auth/login`). Token hết hạn/sai → `401`.
- Tên trường `snake_case`. **Một chỉ số = một trường**, trả **giá trị thô**, không trả chuỗi đã định dạng.
- Đơn vị: thời lượng **giây** (`*_seconds`), độ trễ **mili giây** (`*_ms`), tỷ lệ **phần trăm 0–100** (`*_rate`, `*_pct`), số đếm là số nguyên.
- Ngày `YYYY-MM-DD` (giờ Việt Nam, `Asia/Ho_Chi_Minh`); thời điểm là ISO 8601 có múi giờ, ví dụ `2026-11-15T08:42:00+07:00`.
- Mọi trường chỉ số **được phép `null`** (chưa có dữ liệu / không áp dụng) hoặc vắng mặt; frontend hiện `—`. Danh sách rỗng là `[]`.
- Chỉ số "chưa chắc": thêm trường `<tên_chỉ_số>_is_estimate: true`; frontend hiện nhãn "Chưa chắc" kèm tooltip.
- Chức năng thử nghiệm có kiểm soát: `is_experimental: true` → frontend hiện dấu † và chú thích.
- **Không** có trường nào là thống kê cá nhân. Email danh sách trả về **đã che sẵn** (ví dụ `ng***@gmail.com`). Chỉ `accounts/{id}` và `devices/{id}` là dữ liệu theo từng đối tượng. Trang chi tiết tài khoản mặc định che phần giữa họ tên, số điện thoại, email và địa chỉ bằng `***`; nút con mắt cho phép hiện/ẩn thông tin đầy đủ do API cung cấp.

### Tham số kỳ (mọi endpoint `/admin/stats/*`)

| Tham số | Giá trị |
|---|---|
| `period` | `today` \| `week` \| `month` \| `custom` |
| `from`, `to` | ngày ISO `YYYY-MM-DD`, bao gồm cả hai đầu, giờ Việt Nam. Frontend luôn gửi cả ba tham số; tuần tính Thứ Hai → Chủ Nhật |

Kỳ so sánh (kỳ trước) có cùng số ngày, liền ngay trước `from`.

### Tham số danh sách (`accounts`, `devices`, `stats/errors`)

`page` (từ 1), `page_size` (mặc định 10, tối đa 100), `sort`, `order` (`asc`|`desc`), `q` (tìm theo mã), `status`, và bộ lọc riêng (`firmware_version`, `group`). **Lọc, sắp xếp, phân trang do server làm.** Phản hồi: `{ items, total, page, page_size, ... }`.

### Lỗi (mọi mã khác 2xx)

```json
{ "error": { "code": "not_found", "message": "Không tìm thấy tài khoản." } }
```
`401` (frontend xóa phiên và về `#login`), `403`, `404`, `5xx` đều được xử lý; `GET` được thử lại 1 lần khi lỗi mạng/timeout/5xx.

### Xuất file
Nút xuất (cấu hình `EXPORT_FORMAT` trong `js/config.js`):
- `"xlsx"` (mặc định): dashboard tự tạo file Excel từ dữ liệu đang hiển thị (tiếng Việt đúng ở Excel, WPS, LibreOffice; số là số). Tên file `seevia-{trang}-{from}-{to}.xlsx`. Với danh sách phân trang, dashboard gọi lần lượt từng trang (`page_size=100`) rồi ghép.
- `"csv"`: tự tạo CSV UTF-8 có BOM (một số phần mềm như WPS không nhận BOM nên có thể lỗi font).
- `CSV_SERVER_EXPORT = true`: dashboard gọi cùng endpoint kèm `format=csv` (`Accept: text/csv`, UTF-8 có BOM) và tải nguyên file backend trả về.

---

## Xác thực

### `POST /admin/auth/login`
Body: `{ "email": "string", "password": "string" }`
```json
{ "token": "eyJ...", "expires_in": 3600, "user": { "id": "AD-0001", "display_name": "Khánh Hà", "email_masked": "kh***@gmail.com", "role": "admin_readonly", "timezone": "Asia/Ho_Chi_Minh", "created_at": "2026-11-01", "last_login_at": "2026-11-15T08:02:00+07:00" } }
```
`user` có cùng cấu trúc với `GET /admin/auth/me`. Sai thông tin → `401` `invalid_credentials`. `expires_in`: giây.

### `POST /admin/auth/logout` → `{ "ok": true }`
### `GET /admin/auth/me`
Hồ sơ quản trị viên đang đăng nhập (trang `#profile`, avatar và menu ở header). Đăng xuất: `POST /admin/auth/logout`; frontend xóa phiên và về `#login` kể cả khi API trả lỗi.
| Trường | Kiểu | Ghi chú |
|---|---|---|
| `user.id` | string | Mã quản trị viên |
| `user.display_name` | string | Tên hiển thị; chữ cái đầu dùng cho avatar |
| `user.email_masked` | string | Đã che sẵn |
| `user.avatar_url` | string \| null | Ảnh đại diện (URL `https://…`, đường dẫn tương đối hoặc `data:image/…`); null thì giao diện hiện chữ cái đầu của tên |
| `user.role` | string | `admin_readonly` → "Quản trị viên (chỉ xem)" |
| `user.timezone` | string | IANA, ví dụ `Asia/Ho_Chi_Minh` → "Việt Nam (UTC+7)" |
| `user.created_at` | ngày | |
| `user.last_login_at` | ISO 8601 | null được |
```json
{ "user": { "id": "AD-0001", "display_name": "Khánh Hà", "email_masked": "kh***@gmail.com", "role": "admin_readonly", "timezone": "Asia/Ho_Chi_Minh", "created_at": "2026-11-01", "last_login_at": "2026-11-15T08:02:00+07:00" } }
```

### `POST /admin/auth/password` — đổi mật khẩu của chính mình
Body: `{ "current_password": "string", "new_password": "string" }` → `200 { "ok": true }`.

| Lỗi | Mã HTTP | `error.code` |
|---|---|---|
| Mật khẩu hiện tại sai | `400` | `invalid_current_password` |
| Mật khẩu mới yếu (dưới 8 ký tự, hoặc thiếu chữ, hoặc thiếu số) | `422` | `weak_password` |
| Mật khẩu mới trùng mật khẩu cũ | `422` | `same_password` |

Lưu ý: sai mật khẩu hiện tại **không** trả `401` (frontend coi `401` là hết phiên và đăng xuất). Frontend kiểm tra trước cùng quy tắc (tối thiểu 8 ký tự, có cả chữ và số, nhập lại phải khớp) nhưng backend vẫn phải kiểm tra. Backend được phép thu hồi các token khác sau khi đổi; token đang dùng nên còn hiệu lực để người dùng ở lại trang.

### `PUT /admin/auth/avatar` — tải ảnh đại diện
`multipart/form-data`, trường `file`: ảnh JPEG đã được trình duyệt cắt vuông ở giữa và thu về 256×256 (nhẹ, thường dưới 50 KB). Backend vẫn phải kiểm tra loại tệp (JPEG/PNG/WebP) và dung lượng, và nên tự xử lý lại ảnh. Trả `200 { "user": { …, "avatar_url": "…" } }`. Lỗi: `422 invalid_image`, `413 image_too_large`.

### `DELETE /admin/auth/avatar` — xóa ảnh đại diện
Trả `200 { "user": { …, "avatar_url": null } }`.

---

## Thống kê

### `GET /admin/stats/overview`
| Trường | Kiểu | Ghi chú |
|---|---|---|
| `totals.uses` | int | Lượt sử dụng |
| `totals.active_owners` | int | Chủ kính hoạt động |
| `totals.completion_rate` | number % | |
| `totals.usage_seconds` | int giây | |
| `totals.usage_seconds_per_owner` | int giây | Thời gian sử dụng trung bình mỗi chủ kính |
| `totals.uses_change_pct` | number % | Thay đổi so với kỳ trước (âm = giảm) |
| `totals.active_owners_change` | int | Chênh lệch tuyệt đối |
| `totals.completion_rate_change_points` | number | Chênh lệch **điểm phần trăm** |
| `totals.usage_seconds_change_pct` | number % | |
| `series.granularity` | `"day"`\|`"hour"` | `hour` khi `from == to` |
| `series.points[]` | | `bucket` (ngày hoặc thời điểm giờ), `uses`, `uses_prev`, `active_owners`, `active_owners_prev`, `completion_rate`, `completion_rate_prev`, `usage_seconds`, `usage_seconds_prev` (null được) |
| `groups[]` | | `{ key: see_read\|find\|connect\|go, uses }`. **Tổng kỳ `totals.uses` = tổng `groups[].uses` + `sos_uses`**; mọi "% tổng lượt" tính trên `totals.uses` |
| `sos_uses` | int | Số lần SOS (gồm cả lần bị hủy trong 5 giây); bằng `safety.sos.count` |
| `top_features[]` | | `{ key, uses, completion_rate }` đã xếp giảm dần, tối đa 5 |
| `attention[]` | | `{ type: "error_increase", error_code, feature, delta }`, `{ type: "sos", count, cancelled }`, `{ type: "device_offline", device_id, since }` |

```json
{ "totals": { "usage_seconds_per_owner": 11600, "uses": 301, "active_owners": 3, "completion_rate": 87, "usage_seconds": 34800, "uses_change_pct": 8, "active_owners_change": 0, "completion_rate_change_points": 2, "usage_seconds_change_pct": 12 },
  "series": { "granularity": "day", "points": [ { "bucket": "2026-11-09", "uses": 40, "uses_prev": 36, "active_owners": 2, "active_owners_prev": 2, "completion_rate": 88, "completion_rate_prev": 85, "usage_seconds": 4600, "usage_seconds_prev": 4100 } ] },
  "groups": [ { "key": "see_read", "uses": 148 }, { "key": "find", "uses": 71 }, { "key": "connect", "uses": 58 }, { "key": "go", "uses": 21 } ],
  "sos_uses": 3,
  "top_features": [ { "key": "ocr", "uses": 81, "completion_rate": 88 } ],
  "attention": [ { "type": "error_increase", "error_code": "net_timeout", "feature": "ocr", "delta": 3 },
                 { "type": "sos", "count": 3, "cancelled": 1 },
                 { "type": "device_offline", "device_id": "KINH-02", "since": "2026-11-14T21:10:00+07:00" } ] }
```

### `GET /admin/stats/see-read` · `find` · `connect` · `go`
`summary` (3 thẻ) + `features[]` (bảng và thẻ chi tiết).

`summary` — `see-read`/`find`/`connect`: `uses` int, `share_of_total_pct` %, `completion_rate` %, `usage_seconds` giây. `go`: `uses`, `share_of_total_pct`, `alert_mode_seconds` giây, `alerts_per_hour` number.

`features[]`: `key`, `top_error` `{code, count}` (tùy chọn; giao diện hiện cho Gọi người thân / nhắn tin / đọc tin), `uses` int, `uses_unit` (`uses`\|`sessions`\|`calls`\|`activations`), `completion_rate` % (null nếu không tính), `first_audio_p50_ms` ms (null được), `errors` int, `is_experimental` bool, `details` (object, tên trường theo bảng dưới).

| Nhóm / `key` | Trường trong `details` (đơn vị) |
|---|---|
| see-read / `ocr` | `rejected_images` (int), `avg_retries` (number), `processing_p50_seconds` (giây) |
| `ocr_live` | `framing_p50_seconds`, `avg_session_seconds` |
| `money` | `uncertain_rate_pct` (%), `rejected_images` |
| `ask` | `rejected_images`, `duration_p50_seconds` |
| find / `find`, `space`, `hazard` (†) | `rejected_images`, `duration_p50_seconds` |
| connect / `chat` | `web_lookup_rate_pct`, `duration_p50_seconds` |
| `family` | `cancel_in_window_pct`, `reask_pct`, `confirm_p50_seconds` |
| `internet_call` | `answer_rate_pct`, `avg_call_seconds`, `connection_errors` (int) |
| `music` | `avg_play_seconds`, `cancel_rate_pct` (%) |
| go / `directions` | `avg_trip_seconds`, `cancel_midway_pct` |
| `ride` | `avg_booking_seconds`, `cancel_midway_pct` |
| `alert_mode` | `total_on_seconds`, `alerts_per_hour` |

```json
{ "summary": { "uses": 148, "share_of_total_pct": 49, "completion_rate": 89, "usage_seconds": 11100 },
  "features": [ { "key": "ocr", "uses": 81, "uses_unit": "uses", "completion_rate": 88, "first_audio_p50_ms": 1900, "errors": 4, "is_experimental": false,
                  "details": { "rejected_images": 14, "avg_retries": 1.3, "processing_p50_seconds": 3.4 } },
                { "key": "ocr_live", "uses": 11, "uses_unit": "sessions", "completion_rate": null, "first_audio_p50_ms": null, "errors": 0, "is_experimental": false,
                  "details": { "framing_p50_seconds": 8, "avg_session_seconds": 160 } } ] }
```

### `GET /admin/stats/community`
`install_attempts` int, `install_successes` int, `install_success_rate` %, `install_errors[]` (`{code, count}`, mã (đúng 3 mã S25): `wifi_5ghz` (mạng 5 GHz), `glasses_not_found` (không tìm thấy kính), `link_request_expired` (yêu cầu liên kết hết hạn), `timeout`), `install_p50_seconds` giây, `emergency_contact_accounts` int, `total_accounts` int, `disconnects_per_day` int (số sự kiện "Mất kết nối mạng" trung bình mỗi ngày của cả hệ thống), `battery_to_20_p50_seconds` giây, `glasses_sessions` int, `glasses_session_seconds` giây, `glasses_active_days` int (số ngày có hoạt động).
```json
{ "install_attempts": 5, "install_successes": 4, "install_success_rate": 80, "install_errors": [ { "code": "wifi_5ghz", "count": 1 } ], "install_p50_seconds": 720,
  "emergency_contact_accounts": 2, "total_accounts": 3, "disconnects_per_day": 23, "battery_to_20_p50_seconds": 13800, "glasses_sessions": 27, "glasses_session_seconds": 112500, "glasses_active_days": 6 }
```

### `GET /admin/stats/time`
`peak_hours` `{start_hour, end_hour, secondary_start_hour, secondary_end_hour}` (0–23, null được), `busiest_day` `{date, uses}` (ngày có nhiều lượt nhất trong kỳ; null khi kỳ là một ngày), `weekend_uses` int, `by_day[]` `{date, weekday, uses}`, `by_hour[]` `{hour 0–23, uses}` (null với giờ chưa tới), `by_week[]` `{week_start, week_end, uses}` (tuần Thứ Hai → Chủ Nhật, chỉ khi kỳ dài hơn 7 ngày; tuần đầu/cuối kỳ có thể chưa đủ 7 ngày), `heatmap` `{weekdays:[1..7], hours:[0..23], cells: int[7][24]}` (hàng = thứ, cột = giờ bắt đầu).
```json
{ "peak_hours": { "start_hour": 7, "end_hour": 9, "secondary_start_hour": 17, "secondary_end_hour": 20 },
  "busiest_day": { "date": "2026-11-12", "uses": 60 }, "weekend_uses": 47,
  "by_day": [ { "date": "2026-11-09", "weekday": 1, "uses": 40 } ],
  "heatmap": { "weekdays": [1,2,3,4,5,6,7], "hours": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23], "cells": [[0,0,0,0,0,0,1,4,7,6,3,3,2,2,3,4,6,8,7,5,3,2,1,0]] } }
```
(`cells` rút gọn ví dụ: thực tế đủ 7 hàng × 24 cột.)

### `GET /admin/stats/quality`
`completion` `{rate, rate_change_points, first_try_rate, first_try_change_points, avg_retries, abandon_rate}`; `speed` `{first_audio_p50_ms, first_audio_p90_ms, final_result_p50_ms}`; `total_uses`; `outcomes[]` `{key: completed|completed_early|failed|cancelled|unconfirmed|abandoned, uses, share_pct}`; `attempts_total`; `attempts[]` `{key: reask|image_rejected|interrupted, count, share_pct}`.
```json
{ "completion": { "rate": 87, "rate_change_points": 2, "first_try_rate": 71, "first_try_change_points": 1, "avg_retries": 1.3, "abandon_rate": 6 },
  "speed": { "first_audio_p50_ms": 2100, "first_audio_p90_ms": 4600, "final_result_p50_ms": 9000 }, "total_uses": 301,
  "outcomes": [ { "key": "completed", "uses": 232, "share_pct": 77 } ], "attempts_total": 77, "attempts": [ { "key": "reask", "count": 41, "share_pct": 53 } ] }
```

### `GET /admin/stats/engagement`
`active_owners` (số chủ kính hoạt động trong **kỳ đang chọn**: hôm nay → DAU, tuần này → WAU, tháng này → MAU), `new_users`, `active_glasses`, `total_glasses` (int), `avg_active_days` number, `period_days` int, `granularity` (`day`\|`hour`; `hour` khi kỳ là một ngày), `daily[]` `{date, active_owners}` (`date` là ngày, hoặc thời điểm theo giờ khi `granularity = hour`). "Hoạt động" = có ít nhất 1 lượt sử dụng; kính chỉ kết nối thì không tính.
```json
{ "active_owners": 3, "granularity": "day", "new_users": 1, "active_glasses": 3, "total_glasses": 3, "avg_active_days": 5.3, "period_days": 7,
  "daily": [ { "date": "2026-11-09", "active_owners": 2 } ] }
```

### `GET /admin/stats/safety`
`sos` `{count, success, failed, confirm_p50_seconds, cancelled_within_5s}` — `count` = mọi lần kích hoạt = `success` + `failed` + `cancelled_within_5s`; tỷ lệ thành công/thất bại tính trên các lần không bị hủy; `alert_live` `{alert_mode (vd "controlled_trial"), sessions, total_on_seconds, alerts_per_hour, levels[]: {level 0–3, count, share_pct}}`.
```json
{ "sos": { "count": 3, "success": 2, "failed": 0, "confirm_p50_seconds": 9, "cancelled_within_5s": 1 },
  "alert_live": { "alert_mode": "controlled_trial", "sessions": 8, "total_on_seconds": 12000, "alerts_per_hour": 14,
                  "levels": [ { "level": 0, "count": 27, "share_pct": 58 }, { "level": 1, "count": 13, "share_pct": 27 }, { "level": 2, "count": 5, "share_pct": 11 }, { "level": 3, "count": 2, "share_pct": 4 } ] } }
```

### `GET /admin/stats/errors`
Tham số thêm: `group`, `firmware_version`, `sort` (`code|feature|group|count|change|last_seen`, mặc định `count` giảm dần), `order`, `page`, `page_size`.
```json
{ "items": [ { "code": "net_timeout", "feature": "ocr", "group": "See & Read", "count": 7, "change": 3, "last_seen": "2026-11-15T08:31:00+07:00", "firmware_version": "v0" } ],
  "total": 4, "page": 1, "page_size": 10, "available_groups": ["See & Read", "Connect", "Go"], "available_firmware_versions": ["v0"] }
```
`feature` là khóa chức năng (giao diện hiện tên tiếng Việt, ví dụ `ocr` → "Đọc chữ"). Một mã lỗi có thể xuất hiện ở nhiều chức năng (mỗi cặp mã + chức năng là một dòng). **Tổng `count` mọi dòng = tổng `features[].errors` của 4 trang nhóm.** `change` = chênh lệch số lượt so với kỳ trước (dương = tăng = xấu).

---

## Quản trị (chỉ xem)

### `GET /admin/accounts`
Tham số: `q` (mã), `status` (`active`\|`suspended`), `sort` (`id|created_at|status|glasses_count`), `order`, `page`, `page_size`.
```json
{ "items": [ { "id": "TK-0001", "email_masked": "ng***@gmail.com", "created_at": "2026-11-02", "status": "active", "glasses_count": 1 } ], "total": 4, "page": 1, "page_size": 10 }
```
### `GET /admin/accounts/{id}`
```json
{ "id": "TK-0001", "full_name": "Nguyễn Thị Nga", "phone_number": "0901000001", "email": "khachhang1@example.com", "email_masked": "ng***@gmail.com",
  "gender": "female", "date_of_birth": "1965-01-01", "address": "Phường Bến Thành, TP. Hồ Chí Minh",
  "created_at": "2026-11-02", "status": "active", "glasses_count": 1,
  "glasses": [ { "id": "KINH-01", "firmware_version": "v0", "status": "online", "last_seen_at": "2026-11-15T08:42:00+07:00" } ] }
```
Các trường khách hàng `full_name`, `phone_number`, `email`, `gender`, `date_of_birth`, `address` là tùy chọn (string hoặc null). `gender`: `male`, `female`, `other`, `prefer_not_to_say`; `date_of_birth`: ngày `YYYY-MM-DD`. Trang chi tiết hiển thị “Chưa cập nhật” khi thiếu dữ liệu; khi ẩn ưu tiên `email_masked`, nếu chỉ có `email` thì che trước khi hiển thị. Tên, điện thoại và địa chỉ cũng được che phần giữa, ví dụ `Nguyễn *** Nga`, `090***001`, `Phường *** Hồ Chí Minh`. Nút con mắt hiện đầy đủ cả bốn trường; bấm lại để che. Email đầy đủ lấy từ `email`; nếu API chỉ trả `email_masked` thì giữ giá trị đã che. Trạng thái hiện chỉ áp dụng cho trang đang mở, không lưu lại khi chuyển trang hoặc tải lại. Giá trị gốc chỉ được đưa vào nội dung văn bản khi bấm hiện, không đưa vào tooltip, thuộc tính HTML hay liên kết liên hệ. Các ví dụ trên là dữ liệu giả lập.

### `GET /admin/devices`
Tham số: `q`, `status` (`online`\|`offline`), `firmware_version`, `sort` (`id|firmware_version|status|last_seen_at|account_id`), `order`, `page`, `page_size`.
```json
{ "items": [ { "id": "KINH-01", "firmware_version": "v0", "status": "online", "last_seen_at": "2026-11-15T08:42:00+07:00", "account_id": "TK-0001" } ],
  "total": 3, "page": 1, "page_size": 10, "available_firmware_versions": ["v0"] }
```
### `GET /admin/devices/{id}`
```json
{ "id": "KINH-01", "firmware_version": "v0", "status": "online", "last_seen_at": "2026-11-15T08:42:00+07:00", "account_id": "TK-0001",
  "recent_errors": [ { "code": "net_timeout", "feature": "ocr", "occurred_at": "2026-11-15T08:31:00+07:00" } ] }
```
### `GET /admin/config`
```json
{ "admin_accounts": [ { "email_masked": "kh***@gmail.com", "created_at": "2026-11-01", "last_login_at": "2026-11-15T08:02:00+07:00" } ] }
```

## Ràng buộc nhất quán (dữ liệu mẫu tự kiểm tra trong `js/api/mock/validate.js`)
- Tổng lượt trong kỳ = tổng `series.points[].uses` = tổng `time.by_day[].uses` = tổng ô `time.heatmap.cells` = tổng `quality.outcomes[].uses` = tổng nhóm + `sos_uses`.
- `summary.uses` của từng trang nhóm = `groups[].uses` tương ứng ở Tổng quan = tổng `features[].uses` của trang đó.
- `sos.count` (An toàn) = `sos_uses` (Tổng quan) = `success` + `failed` + `cancelled_within_5s`.
- Bốn `levels[].share_pct` cộng lại = 100. `total_accounts` = tổng số tài khoản trong danh sách.

---

## Nối backend thật (SeeVia AB)

AB (app server, FastAPI) phục vụ toàn bộ hợp đồng này dưới `/api/v1/admin/...`.

**Bật:** `js/config.js` → `USE_MOCK = false`, `API_BASE_URL = "https://<AB>/api/v1"`. Dashboard khác origin với AB thì đặt
`ADMIN_CORS_ORIGINS='["https://<origin dashboard>"]'` cho AB. Tạo tài khoản admin trên máy AB (mật khẩu nhập qua stdin):
`uv run python scripts/create_admin.py --email <email> --name "<tên>"`. Phiên đăng nhập 1 giờ; sai 5 lần/15 phút → `429 too_many_attempts`.

**Chỗ AB khác dữ liệu mẫu:**

| Mục | AB trả |
|---|---|
| Tài khoản | AB đăng nhập bằng số điện thoại: `email_masked: null` và thêm `phone_masked` (`09******12`). Dashboard hiện `email_masked`, không có thì hiện `phone_masked` |
| Mã hiển thị | `id` tài khoản = mã người dùng của AB, `id` kính = mã thiết bị kính (không phải `TK-xxxx`/`KINH-xx`) |
| `engagement.new_users` | Tài khoản mới tạo trong kỳ (như hợp đồng); thêm `new_active_users` = chủ kính có lượt dùng đầu tiên trong kỳ |
| Cài đặt kính, pin | `install_*`, `battery_to_20_p50_seconds` luôn `null`, `install_errors: []` (chưa có nguồn đo) |
| Mức cảnh báo 0 | Luôn `0` (kính không phát cảnh báo "an toàn"); `share_pct` tính trên mức 1–3 vẫn cộng = 100, không có cảnh báo thì `null` |
| Lỗi tham số | `400 invalid_request` (vd. `from > to`, kỳ > 400 ngày) |

**Cách đếm "lượt" (`uses`) ở AB:** một tác vụ đã có kết cục cuối, hoặc một lần bật cảnh báo vật cản, hoặc một cuộc gọi Internet
người thân gọi tới kính đã kết thúc, hoặc một lần SOS đã kết thúc. Mỗi lượt có đúng một trạng thái, một ngày và một giờ bắt đầu,
nên mọi ràng buộc nhất quán ở trên đều đúng với dữ liệu thật. `ocr_live` là các lần đọc chữ phải căn khung trực tiếp (không cộng
trùng với `ocr`). `features[].errors` và bảng lỗi chỉ đếm lượt thất bại/lỗi kỹ thuật có mã; lượt bị huỷ không phải lỗi.
Tỷ lệ hoàn thành, hoàn thành ngay lần đầu, bỏ cuộc tính trên các lượt là tác vụ (không gồm cảnh báo, cuộc gọi đến, SOS).

Trang chi tiết dùng `phone_masked` khi AB chưa cung cấp `phone_number`. Nút hiện thông tin chỉ hiển thị giá trị gốc nếu API có trả về.

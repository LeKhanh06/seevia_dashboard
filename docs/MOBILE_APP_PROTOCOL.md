# Giao thức app điện thoại "Your Eyes" ↔ App Communication Server

Tài liệu cho người clone repo, build app Android (`mobile/`) và nối vào app server (`server/`) để chạy
các task. Nguồn sự thật là code: `server/src/app/`, `contracts/device-api.openapi.yaml`,
`mobile/app/src/main/java/com/youreyes/app/`. Hợp đồng nghiệp vụ phía server kính nằm ở
[`external-client-api.md`](external-client-api.md); luồng tổng thể ở [`architecture.md`](architecture.md).

```text
server kính ──POST /api/v1/service/*──► app server ──FCM data──► điện thoại
                                            ▲                        │
                                            └── POST /api/v1/device/report, /event ──┘
```

Hai server, hai base URL, hai loại token, đừng nhầm:

| | URL mặc định | Token | Dùng cho |
|---|---|---|---|
| App server | `https://app.visioncare-host.uk` | Device API token (dùng chung, server chỉ lưu hash) | mọi thứ trong tài liệu này |
| Server kính | `https://api.visioncare-host.uk` | token server kính | trạng thái kính, đổi Wi-Fi/firmware (`GlassesServerClient`), ngoài phạm vi giao thức lệnh |

## 1. Bắt đầu nhanh

1. Clone repo, mở `mobile/`. Cần JDK 17 + Android SDK (`mobile/scripts/bootstrap-android.ps1` cài cục bộ
   nếu thiếu). `minSdk 24`, `targetSdk 36`.
2. Lấy `google-services.json` của dự án Firebase đặt ở `mobile/app/google-services.json` (đã `.gitignore`).
   Thiếu file này thì không có FCM token và không nhận được lệnh.
3. Tạo `mobile/local.properties` (không commit) hoặc truyền `-Pyoureyes.<khoá>=...`:

   | Khoá | Mặc định | Ý nghĩa |
   |---|---|---|
   | `youreyes.serverUrl` | `https://app.visioncare-host.uk` | App server; điền sẵn vào form đăng ký |
   | `youreyes.deviceApiToken` | trống | Device API token thô |
   | `youreyes.glassesServerUrl` | `https://api.visioncare-host.uk` | Server kính |
   | `youreyes.glassesServerToken` | trống | Token server kính |
   | `youreyes.spotifyClientId` | client id demo | Spotify App Remote (không phải secret) |

4. Token lấy ở đâu: file `SeeVia_system/.env` do `demo.sh` sinh. Device API token thô là biến
   `DEVICE_API_TOKEN` (server đọc bản băm `DEVICE_API_TOKEN_HASH`). Token server kính là nhóm biến
   `VISIONCARE_*_TOKEN` (app dùng token của client thiết bị, `VISIONCARE_DEVICE_CLIENT_TOKEN`). Không
   commit các giá trị này; để trống `deviceApiToken` thì app hỏi token trên màn hình.
5. Build: `Set-Location mobile; .\gradlew.bat test lint assembleDebug`. Bản debug cho phép HTTP thường
   (emulator gọi máy host qua `http://10.0.2.2:8000`); bản release chỉ HTTPS.
6. Mở app, cấp quyền khi được hỏi (gọi điện, danh bạ, SMS, vị trí, thông báo, hiển thị trên ứng dụng khác,
   bỏ tối ưu pin). Bật "Truy cập thông báo" cho Your Eyes để điều khiển Spotify. Cần cài sẵn Spotify, Google
   Maps, Grab. Chi tiết quyền: `mobile/app/src/main/AndroidManifest.xml`.

Chạy app server cục bộ: xem [`../server/README.md`](../server/README.md) (`DELIVERY_TRANSPORT=fake|fcm|webhook`).

## 2. Đăng ký và ghép

Mọi endpoint `/api/v1/device/*` dùng header `Authorization: Bearer <Device API token>`. Thiếu hoặc sai token
trả `401 {"detail":"UNAUTHORIZED"}` (không dùng envelope lỗi chuẩn). Các endpoint còn lại dùng envelope:

```json
{ "status": "ok", "data": { } }
{ "status": "error", "error": { "code": "INVALID_REQUEST", "message": "Request validation failed", "details": {} } }
```

Body sai schema (trường thừa, thiếu) trả `400 INVALID_REQUEST`; mọi schema đều `extra="forbid"`.

### 2.1. Tài khoản người dùng (tùy chọn nhưng là đường bình thường)

Không cần Device token, nguồn: `server/src/app/api/auth.py`, `DeviceApiClient.kt`.

| Bước | Endpoint | Body | `data` |
|---|---|---|---|
| Đăng ký | `POST /api/v1/auth/register` | `{phone_number, password (6-200 ký tự), display_name?}` | `{user_id, public_user_id, phone_number, otp_required:true}` |
| Xác minh OTP | `POST /api/v1/auth/otp/verify` | `{phone_number, otp_code}` | `{access_token, user_id, public_user_id, phone_number, display_name}` |
| Đăng nhập | `POST /api/v1/auth/login` | `{phone_number, password}` | như trên |
| Đăng xuất | `POST /api/v1/auth/logout` | `{}`, Bearer = `access_token` | `{logged_out:true}` |

Prototype không gửi SMS: mã OTP được ghi vào log server (`Demo OTP for <số>: <mã>`). `access_token` chỉ trả
một lần. Sau khi đăng nhập app ghi đè `user_id` của máy thành `public_user_id`; đây chính là `user_id` dùng ở
mọi bước dưới. Lỗi thường gặp: `409 PHONE_ALREADY_REGISTERED`, `401 INVALID_CREDENTIALS`,
`403 PHONE_NOT_VERIFIED`, `400 OTP_INVALID|OTP_EXPIRED`.

### 2.2. Đăng ký điện thoại và FCM token

`POST /api/v1/device/register` (Device token):

```json
{ "user_id": "user-123", "device_id": "device-123", "platform": "android", "push_token": "<FCM registration token>" }
```

Trả `200 {"status":"ok","data":{"device_id":"device-123","registered":true}}`.

- `user_id`, `device_id` là chuỗi tự do (app mặc định `user-100` / `device-100`, nút "tạo mới" sinh
  `user-NNN` / `device-NNN`). `platform` chỉ nhận `android`.
- Gọi lại cùng `device_id` + cùng user là idempotent và dùng để xoay push token. Lỗi:
  `409 DEVICE_OWNER_MISMATCH` (device_id đang thuộc user khác), `403 DEVICE_REVOKED`.
- App lấy token bằng `FirebaseMessaging.getInstance().token`, chỉ lưu `base_url`, `user_id`, `device_id`,
  `fcm_token`, `bearer_token` vào SharedPreferences sau khi register thành công. Khi FCM đổi token
  (`FcmPushReceiver.onNewToken`) app tự đăng ký lại bằng đúng các giá trị đã lưu.
- Server giao lệnh tới điện thoại active mới nhất của `user_id`.

### 2.3. Ghép kính

Server kính nói chuyện bằng `device_id` của kính (dạng `glasses-<số seri>`; app thêm tiền tố `glasses-`,
đổi chữ thường và bỏ `#` nếu người dùng chỉ gõ số seri). Ghép kính với user:

`POST /api/v1/device/glasses/link` (Device token): `{"user_id":"user-123","device_id":"glasses-001"}` →
`{"status":"ok","data":{"device_id":"glasses-001","linked":true}}`. Kính đã thuộc user khác:
`409 GLASSES_DEVICE_OWNER_CONFLICT`.

`POST /api/v1/device/glasses/unlink`: `{"user_id":"user-123"}` →
`{"status":"ok","data":{"unlinked":true}}` (`false` nếu không có gì để gỡ, không phải lỗi).

Server kính gọi `POST /api/v1/service/*` kèm `device_id` của kính; nếu chưa ghép thì bị
`404 GLASSES_DEVICE_NOT_LINKED`, còn nếu đã ghép mà user chưa có điện thoại đăng ký thì lệnh nằm ở
`processing` tới khi hết hạn (mục 3.3).

Ngoài ra có `POST /api/v1/device/link` (Bearer = `access_token` của user, không phải Device token) để xác nhận
điện thoại đã đăng ký thuộc user đang đăng nhập, trả `{device_id, platform, linked:true}`; app hiện chưa gọi.

### 2.4. BLE: cấp Wi-Fi cho kính (không phải ghép)

BLE chỉ dùng để đưa Wi-Fi cho kính khi kính mất mạng (`GlassesBleProvisioner`); việc ghép kính với user
luôn đi qua `glasses/link` ở trên. Kính chỉ quảng bá BLE khi không vào được mạng nào (sau 3 lần thử Wi-Fi,
khoảng 90 giây).

| Mục | Giá trị |
|---|---|
| Lọc quét | theo UUID dịch vụ, không theo tên: `6e400001-b5a3-f393-e0a9-e50e24dcca9e` (Nordic UART) |
| Ghi cấu hình | characteristic RX `6e400002-...`, JSON `{"ssid":"...","password":"..."}`, `WRITE_TYPE_DEFAULT` |
| Kết quả | kính notify câu trả lời trên TX `6e400003-...` (bật CCCD `00002902-...` trước), rồi tự khởi động lại |
| Bắt buộc | xin MTU 247 trước khi ghi (firmware không ghép mảnh); không dùng `autoConnect` |

Hai đường khác trong `GlassesServerClient`: qua server kính (`POST /device/wifi`) khi kính còn online, hoặc
trực tiếp `http://<ip kính>/wifi/config` (kính phát hotspot `VisionCare-Setup`, IP `192.168.4.1`).

## 3. Nhận lệnh (FCM data-only)

### 3.1. Thông điệp

App server gửi FCM **data message** (không có khối `notification`), `android.priority=high`, `ttl=10s`.
Mọi giá trị là chuỗi. Dựng ở `_command_data` (`server/src/app/adapters/delivery.py`), dùng chung cho FCM và
webhook của `phone_sim`. Payload không mang token hay khoá nào; điện thoại báo kết quả bằng URL và token đã
lưu lúc đăng ký.

| Trường | Ý nghĩa |
|---|---|
| `request_id` | UUID của thao tác; khoá chống chạy trùng, chép nguyên vào report |
| `user_id` | chép nguyên vào report |
| `device_id` | id **kính** (không phải id điện thoại), chép nguyên vào report |
| `action` | một trong 13 action (mục 4) |
| `params_json` | tham số dạng chuỗi JSON, thiếu hoặc rỗng thì app coi là `{}` |
| `issued_at` | lúc server tạo lệnh (ISO-8601 UTC) |
| `deadline_at` | hiện luôn bằng `issued_at` + 1 giây, chỉ mang tính tham khảo, app bỏ qua |
| `expires_at` | lúc lệnh hết hạn; sau đó server không nhận report nữa |
| `requested_priority` | luôn `high` |
| `maps_url` | chỉ có ở `navigation_start`: link Google Maps chỉ đường đi bộ (mục 4.1) |

Ví dụ `navigation_start` (từ `server/tests/fixtures/server_payloads/navigation_start.json`):

```json
{
  "request_id": "3f1c9a40-5b7e-4c2d-9a10-000000000006",
  "user_id": "user-123",
  "device_id": "glasses-001",
  "action": "navigation_start",
  "params_json": "{\"destination\":{\"address\":\"Bưu điện Thành phố Hồ Chí Minh\"}}",
  "issued_at": "2026-10-19T08:00:00.000000+00:00",
  "deadline_at": "2026-10-19T08:00:01.000000+00:00",
  "expires_at": "2026-10-19T08:01:00.000000+00:00",
  "requested_priority": "high",
  "maps_url": "https://www.google.com/maps/dir/?api=1&destination=B%C6%B0u%20%C4%91i%E1%BB%87n%20Th%C3%A0nh%20ph%E1%BB%91%20H%E1%BB%93%20Ch%C3%AD%20Minh&travelmode=walking"
}
```

(Các fixture khác trong thư mục đó cho mọi action còn lại; ví dụ `contact_call` có
`"params_json": "{\"name\":\"Mẹ\"}"`.)

`PushCommand.fromData` (`CommandDispatcher.kt`) coi lệnh hợp lệ khi `request_id`, `user_id`, `device_id`,
`action` đều không rỗng; thiếu một khoá thì bỏ qua thông điệp (chỉ ghi log). Hiện app chỉ đọc 5 khoá
`request_id, user_id, device_id, action, params_json`; các khoá còn lại bị bỏ qua. `maps_url` là khoá mới
nên cần chuyển thêm qua `CommandExecutionService` cùng thay đổi `navigation_start` ở mục 4.1.

### 3.2. Luồng trên máy

`FcmPushReceiver` (chỉ parse) → `CommandExecutionService` (foreground service, chạy từng lệnh một) →
`ReportFlusher` gửi lại report còn treo → `CommandDispatcher` → `ActionRegistry` chọn handler → lưu report
→ gửi `POST /api/v1/device/report`. Action lạ trả `failed / UNSUPPORTED_ACTION`; handler ném ngoại lệ trả
`failed / EXECUTION_FAILED`.

### 3.3. Hạn, TTL, retry

- Hạn của từng action (server tính `expires_at = created_at + timeout`): `ride_quote` 60s, `ride_confirm`
  90s, `music_play` 45s, `music_stop` 30s, `music_volume` 30s, `navigation_start` 90s, `navigation_stop`
  45s, `emergency_call` 21 phút, `contact_call` 60s, `location_get` 30s, `capabilities_get` 30s,
  `call_answer` 20s, `call_reject` 20s (`server/src/app/actions.py`).
- Quá `expires_at` mà chưa có report, `TimeoutWorker` chuyển thao tác sang `timed_out / REPORT_TIMEOUT`;
  report đến sau đó bị `409`.
- FCM TTL chỉ 10 giây: máy tắt hoặc mất mạng quá 10 giây thì FCM bỏ thông điệp, thao tác sẽ hết hạn.
- Lỗi gửi tạm thời (mạng, 5xx, quota) server thử lại sau 5s rồi 20s; token FCM chết (`Unregistered`) thì
  thao tác `failed / DELIVERY_FAILED` ngay.

### 3.4. Xử lý trùng

Server giao **at-least-once**, app thực thi **đúng một lần**: `CommandStore.insertCommandIfNew` ghi
`request_id` vào SQLite trước khi chạy handler; trùng thì bỏ qua (`DuplicateIgnored`), không chạy lại và
không gửi lại report. Handler chạy bằng foreground service nên không bị kill giữa chừng khi chờ kết quả thật.

## 4. Các action

Quy ước: handler chỉ báo `succeeded` khi điện thoại xác nhận kết quả thật (cuộc gọi đã off-hook, nhạc đã
phát ≥ 4 giây...). Thiếu app hoặc quyền thì `failed` với mã rõ ràng, không báo thành công giả.
`destination` là `{address?, lat?, lng?}`: cần `address` hoặc cặp `lat`+`lng` đầy đủ. Số điện thoại trong
kết quả luôn che dạng `***768`. Mã lỗi nằm trong `error.code`; `error.details` mặc định `{}`.

| Action | `params` | Việc app làm | `result` khi `succeeded` | Mã lỗi |
|---|---|---|---|---|
| `ride_quote` | `current_location{lat,lng}`, `destination` | Geocode đích nếu thiếu toạ độ, mở Grab ở màn đặt xe, lưu quote 5 phút | `quote_id`, `product_type:"GrabCar"`, `price_estimate{currency:"VND",amount:85000}`, `eta_minutes:6`, `expires_at` (giá và ETA là số demo cố định) | `INVALID_DESTINATION`, `RIDE_PROVIDER_ERROR` |
| `ride_confirm` | `quote_id`, `confirm` | `confirm=true`: mở lại Grab; `false`: huỷ quote. Mỗi quote dùng một lần | `{quote_id, ride_id, ride_status:"requested"}` hoặc `{quote_id, ride_id:null, ride_status:"cancelled"}` | `QUOTE_NOT_FOUND`, `QUOTE_EXPIRED`, `QUOTE_ALREADY_USED`, `RIDE_CONFIRM_FAILED` |
| `music_play` | `song` (`"Tên - Nghệ sĩ"`), `volume?` 0-100, `spotify_uri?` | Đặt âm lượng, phát qua Spotify App Remote hoặc media session; chờ tới khi đúng bài đang phát | `track_id` (URI hoặc `null`), `title`, `artist`, `playback_state:"playing"`, `provider:"Spotify"`, `volume` | `SONG_NOT_FOUND`, `MUSIC_ACCOUNT_NOT_CONNECTED`, `SUBSCRIPTION_INACTIVE`, `PLAYBACK_FAILED` |
| `music_stop` | `{}` | Tạm dừng các media session; nhạc vốn đã tắt vẫn coi là thành công | `{playback_state:"stopped"}` | `PLAYBACK_STOP_FAILED` |
| `music_volume` | đúng một trong `level` 0-100 hoặc `direction` `up`/`down` | Đặt `STREAM_MUSIC`; `direction` đổi một bước = max/10 | `{volume_state:"changed", level}` | `INVALID_VOLUME`, `VOLUME_CHANGE_FAILED` |
| `navigation_start` | `destination` | **Hiện thông báo chỉ đường** (mục 4.1) | `{navigation_id, navigation_state:"navigating", travel_mode:"walking", destination{address,lat,lng}}` | `INVALID_DESTINATION`, `NAVIGATION_PROVIDER_ERROR`, `NAVIGATION_START_FAILED` |
| `navigation_stop` | `navigation_id` | Đánh dấu phiên đã dừng (không đóng được Maps) | `{navigation_id, navigation_state:"stopped"}` | `NAVIGATION_NOT_FOUND`, `NAVIGATION_ALREADY_STOPPED`, `NAVIGATION_STOP_FAILED` |
| `emergency_call` | `{}` | Nhắn SMS `[SOS]` kèm link vị trí cho liên hệ khẩn cấp cấu hình trên máy (Safety), đồng thời gọi | `{emergency_state:"completed", attempt:1, answered:false, cycle:"stopped", contact, sms_sent}` (một lượt, chưa có chu kỳ 21 phút) | `EMERGENCY_CONTACT_NOT_CONFIGURED`, `CALL_PERMISSION_DENIED`, `CALL_FAILED` |
| `contact_call` | `name` (so khớp không phân biệt hoa thường, không bỏ dấu) | Tìm đúng một liên hệ, gọi bằng `TelecomManager`, chờ máy off-hook tối đa 45 giây | `{call_state:"calling", contact_name, phone_number}` | `CONTACT_NOT_FOUND`, `MULTIPLE_CONTACTS_FOUND` (`details.candidates[{name,phone_number}]`), `CONTACT_PERMISSION_DENIED`, `CALL_PERMISSION_DENIED`, `CALL_FAILED` |
| `location_get` | `{}` | Lấy vị trí hiện tại, reverse-geocode địa chỉ tiếng Việt | `{lat, lng, address (hoặc null), captured_at}` | `LOCATION_PERMISSION_DENIED`, `CURRENT_LOCATION_UNAVAILABLE` |
| `capabilities_get` | `{}` | Báo quyền và cài đặt hiện có | `{capabilities{notification_listener, system_alert_window, battery_optimization_off, background_location ("never"/"while_using"/"always"), fine_location, read_contacts, read_phone_state, call_phone, send_sms, emergency_contact_set}}` | `INVALID_PARAMS` |
| `call_answer` | `{}` | Nhấc máy cuộc gọi đang đổ chuông (Android 8+) | `{call_state:"answered"}` | `NO_INCOMING_CALL`, `CALL_PERMISSION_DENIED`, `CALL_CONTROL_UNSUPPORTED`, `CALL_CONTROL_FAILED` |
| `call_reject` | `{}` | Từ chối cuộc gọi đang đổ chuông (Android 9+) | `{call_state:"rejected"}` | như `call_answer` |

Ghi chú: server còn nhận `contact_id` (`contact_call`) và `spotify_uri` (`music_play`, do server tự tra
Spotify rồi thêm vào `params`); app hiện bỏ qua `contact_id`. Cấu trúc `result` của từng action khớp
`server/src/app/schemas/service_results.py` và `contracts/contract-matrix.md`.

Ví dụ `result` của `contact_call` và `error` của `MULTIPLE_CONTACTS_FOUND`:

```json
{ "call_state": "calling", "contact_name": "Mẹ", "phone_number": "***567" }
```

```json
{ "code": "MULTIPLE_CONTACTS_FOUND", "message": "Multiple contacts matched",
  "details": { "candidates": [ { "name": "Nam", "phone_number": "***111" }, { "name": "Nam", "phone_number": "***922" } ] } }
```

### 4.1. `navigation_start`: thông báo chỉ đường (thay đổi so với hiện tại)

**Hành vi hiện tại** (`NavigationActions.kt`): app tự mở Google Maps (`google.navigation:q=...&mode=w`, hoặc
`geo:` nếu thiếu Maps) qua full-screen notification, đợi rồi báo `succeeded`. Cách này hay bị Android chặn mở
activity từ nền nên người dùng không thấy gì.

**Hành vi mới:**

1. Server thêm `maps_url` vào FCM data (top-level, cạnh `params_json`):
   `https://www.google.com/maps/dir/?api=1&destination=<urlencode(address, hoặc "lat,lng")>&travelmode=walking`.
   Dùng `address` nếu có, ngược lại `"lat,lng"`.
2. App hiện một thông báo heads-up (kênh `appdemo_commands`) với tiêu đề **"Chỉ đường tới {address}"** và nội
   dung **"chạm để mở Google Maps"**. `{address}` lấy từ `destination.address`, thiếu thì dùng `lat,lng`. App
   **không** tự mở Maps nữa.
3. Chạm thông báo chạy `PendingIntent` mở Maps: nếu cài Google Maps thì dùng
   `google.navigation:q=<đích>&mode=w` (đích là `address` hoặc `lat,lng`, đã url-encode, `setPackage`
   `com.google.android.apps.maps`); ngược lại dùng `ACTION_VIEW` với `maps_url`. Thiếu `maps_url` (server cũ) thì
   app tự dựng URL theo công thức trên từ `params_json`.
4. App báo `succeeded` **ngay khi thông báo đã hiện**, không chờ người dùng chạm. `result` giữ nguyên schema
   (`navigation_id`, `navigation_state:"navigating"`, `travel_mode:"walking"`, `destination`); toạ độ lấy từ
   geocode, null nếu geocode lỗi.
5. Không hiện được thông báo (tắt quyền thông báo, không có app nào mở được link) thì báo `failed` với
   `NAVIGATION_PROVIDER_ERROR`; thiếu đích hợp lệ thì `INVALID_DESTINATION` (hai mã này là quy ước đề xuất
   cho thay đổi, dùng lại mã có sẵn trong hợp đồng).

Phía server kính: khi thao tác `succeeded`, server kính nói "Đã gửi chỉ đường tới {address} về điện thoại. Mở
thông báo để đi bằng Google Maps." Lệnh `navigation_stop` vẫn dùng `navigation_id` đã trả.

## 5. Báo kết quả

`POST /api/v1/device/report` (Device token), một lần cho mỗi lệnh, ở trạng thái cuối:

```json
{
  "user_id": "user-123",
  "device_id": "glasses-001",
  "request_id": "3f1c9a40-5b7e-4c2d-9a10-000000000006",
  "action": "navigation_start",
  "execution_state": "succeeded",
  "result": {
    "navigation_id": "nav-1a2b3c4d",
    "navigation_state": "navigating",
    "travel_mode": "walking",
    "destination": { "address": "Bưu điện Thành phố Hồ Chí Minh", "lat": 10.7799, "lng": 106.6999 }
  },
  "timestamp": "2026-10-19T08:00:03Z"
}
```

```json
{
  "user_id": "user-123",
  "device_id": "glasses-001",
  "request_id": "3f1c9a40-5b7e-4c2d-9a10-000000000001",
  "action": "contact_call",
  "execution_state": "failed",
  "error": { "code": "CONTACT_NOT_FOUND", "message": "No contact matched 'Mẹ'", "details": {} },
  "timestamp": "2026-10-19T08:00:02Z"
}
```

Quy tắc (`schemas/device.py`, `services/device.py`):

- `execution_state` chỉ `succeeded` hoặc `failed`. `succeeded` bắt buộc có `result` và `error=null`;
  `failed` bắt buộc có `error` (`{code,message,details}`) và `result=null`. Trường rỗng thì app bỏ hẳn khoá.
- `user_id`, `device_id`, `request_id`, `action` phải khớp lệnh đã giao, sai thì `404 REQUEST_NOT_FOUND`.
  `timestamp` phải có múi giờ (app gửi UTC dạng `yyyy-MM-ddTHH:mm:ssZ`).
- Trả `200 {"status":"ok","data":{"request_id":"...","report_received":true}}`.
- **Idempotent**: gửi lại đúng nội dung (hash của `execution_state`+`result`+`error`) thì `200`, không đổi gì.
  Nội dung khác khi thao tác đã kết thúc (kể cả `timed_out`) thì `409 REQUEST_ID_CONFLICT`.
- Trạng thái công khai: `processing` → `succeeded` | `failed` | `timed_out`; server kính đọc qua
  `GET /api/v1/requests/{request_id}` hoặc callback.

**Retry trên máy** (`CommandStore`, `ReportFlusher`): report được lưu `PENDING` trước khi gửi (timeout HTTP
5 giây). Gửi lỗi thì thành `FAILED` và `ReportFlusher.flush` thử lại **trước mỗi lệnh mới** (không có bộ đếm
giờ nền), tối đa 8 lần rồi `ABANDONED`. Report chỉ được gửi lại, không bao giờ chạy lại handler. Report bị
server từ chối vĩnh viễn (ví dụ đã hết hạn) cũng đi hết 8 lần rồi bị bỏ.

## 6. Sự kiện điện thoại → server

### 6.1. Cuộc gọi đến

`IncomingCallMonitor` theo dõi trạng thái cuộc gọi (cần `READ_PHONE_STATE`, `READ_CALL_LOG` để có số) và gọi
`POST /api/v1/device/event` (Device token). `device_id` ở đây là id **điện thoại** đã đăng ký; server tự suy ra
kính đã ghép.

```json
{ "device_id": "device-123", "type": "call_incoming",
  "caller": { "contact_id": "2", "name": "Mẹ", "number_tail": "1222", "duplicate_name": false } }
```

```json
{ "device_id": "device-123", "type": "call_ended" }
```

- `call_incoming` bắt buộc có `caller`; `call_ended` không được có `caller`.
- Quyền riêng tư: không bao giờ gửi số đầy đủ. `number_tail` đúng 3-4 chữ số; `name` có từ 5 chữ số trở lên
  (giống số điện thoại) bị từ chối. Số lạ thì app gửi `name:"Số lạ"`.
- Phản hồi: `{"status":"ok","data":{"device_id":"device-123","glasses_device_id":"glasses-001","event_forwarded":true}}`.
  Server lọc theo cài đặt riêng tư rồi chuyển tiếp tới `<origin của CALLBACK_URL>/internal/device-events`; lỗi
  chuyển tiếp trả `503 DEVICE_EVENT_FORWARD_FAILED`.
- Trong lúc máy đổ chuông, server kính có thể gửi `call_answer` / `call_reject` (mục 4).

### 6.2. Vị trí và capabilities

Không có sự kiện tự phát. Hai thông tin này là **lệnh** `location_get` và `capabilities_get` (mục 4), điện
thoại chỉ trả lời khi được hỏi.

## 7. Gọi điện / nhắn tin với kính

Người khiếm thị nói và nghe **ngay trên kính**; người thân dùng chính app này. Cuộc gọi và tin nhắn đi **qua internet
giống Messenger**: gọi thoại bằng WebSocket `WS /v2/call` (PCM16) và báo cuộc gọi/tin bằng FCM. **Không dùng SIM, không gọi
số điện thoại, không SMS, không có video call.** Vì vậy app người thân:

- **Chỉ cần quyền micro (`RECORD_AUDIO`) và quyền thông báo (`POST_NOTIFICATIONS`, kèm thông báo toàn màn hình/`USE_FULL_SCREEN_INTENT`
  để màn hình cuộc gọi hiện khi khoá máy).** Trên iOS: micro + thông báo (+ CallKit/PushKit nếu muốn đổ chuông như cuộc gọi thường, tuỳ app).
- **KHÔNG cần quyền camera, KHÔNG cần quyền gọi điện thoại (`CALL_PHONE`)**, không cần danh bạ/SMS cho tính năng này.
- Server **không có** trường hay endpoint video nào; đừng thêm.

Hai server (như mục đầu tài liệu): mọi URL dưới đây là **app server** (AB). Âm thanh KHÔNG đi qua AB mà qua server kính (VH) theo `media_url`.
Xác thực: `Authorization: Bearer <access_token>` của phiên người dùng (mục 2.1, đăng nhập bằng số điện thoại), **không** phải Device API token.
Điện thoại vẫn phải `POST /api/v1/device/register` (mục 2.2) với `user_id` = `public_user_id` của tài khoản để nhận FCM; server đẩy tới
điện thoại đăng ký gần nhất của user. Mọi phản hồi thành công: `{"status":"ok","data":{...}}`; lỗi theo envelope ở mục 2.

```text
        đổ chuông / tin (FCM data)                       âm thanh (WS /v2/call, PCM16)
 kính ─► VH ─► AB ───────────────► app người thân          kính ◄─► VH ◄──────► app người thân
        ▲        ◄─ REST /api/v1/care|calls|messages ──
```

### 7.1. Quan hệ người thân (`care_links`)

Chủ kính mời người thân bằng số điện thoại; người thân phải đã có tài khoản và **chấp nhận** thì mới gọi/nhắn được.

| Việc | Endpoint (Bearer app session) | Body | `data` |
|---|---|---|---|
| Mời (chủ kính) | `POST /api/v1/care/invite` | `{phone_number, display_name, aliases:[str] (≤10), emergency:bool, priority:int 0-100}` | `CareLink` (bên dưới), `status:"pending"` |
| Chấp nhận (người thân) | `POST /api/v1/care/{link_id}/accept` | không | `CareLink`, `status:"active"` |
| Danh sách (cả hai vai) | `GET /api/v1/care/links` | | `{links:[CareLink]}` (ẩn link đã thu hồi) |
| Sửa (chủ kính) | `PATCH /api/v1/care/{link_id}` | `{aliases?, emergency?, priority?}` | `CareLink` |
| Thu hồi (cả hai bên) | `DELETE /api/v1/care/{link_id}` | | `CareLink`, `status:"revoked"` |

```json
{ "status": "ok", "data": { "id": "9c1f0d0e-6c43-4b8e-9f41-0d5b6a3f2a11", "role": "owner", "status": "active",
  "display_name": "Mẹ", "aliases": ["mẹ", "má"], "emergency": true, "priority": 0,
  "owner_user_id": "user-A1B2C3", "relative_user_id": "user-D4E5F6",
  "peer_display_name": "Mẹ Hoa", "peer_phone_number": "0911000001", "created_at": "2026-10-20T07:10:00Z" } }
```

- `role` là vai của NGƯỜI GỌI API trong link (`owner` = chủ kính, `relative` = người thân). `owner_user_id`/`relative_user_id` là `public_user_id`.
- `display_name` + `aliases` là tên chủ kính dùng để gọi ("gọi cho mẹ"). Khớp không phân biệt hoa/thường, bỏ dấu câu; ưu tiên khớp đúng dấu rồi mới khớp
  không dấu. Hai người cùng tên gọi → kính hỏi lại "Bạn muốn gọi ai: A hay B?".
- `emergency:true` + `priority` (nhỏ gọi trước): danh sách khẩn cấp (mục 7.5).
- Lỗi: `404 USER_NOT_FOUND` (số chưa đăng ký), `404 CARE_LINK_NOT_FOUND` (không phải bên liên quan, cũng dùng khi không có quyền để không lộ id),
  `409 CARE_LINK_CONFLICT` (đã mời/đang active, tự mời mình).
- Thu hồi link: mọi cuộc gọi đang chuông/đang nói của cặp đó bị kết thúc (`reason:"revoked"`), tin chưa giao bị huỷ.

**Màn hình app:** (1) danh sách người thân + lời mời đang chờ (nút Chấp nhận); (2) chủ kính: form mời (số điện thoại, tên hiển thị, tên gọi/alias, bật khẩn cấp, thứ tự ưu tiên).

### 7.2. Cuộc gọi

Trạng thái cuộc gọi trên AB: `ringing → active → ended`, hoặc `missed` (hết chuông, mặc định 30 s), `rejected`, `failed`. Hai chiều:
`to_relative` (kính gọi người thân) và `to_glasses` (người thân gọi kính).

| Việc | Endpoint | Body | `data` |
|---|---|---|---|
| Gọi tới kính | `POST /api/v1/calls` | `{owner_user_id}` (cần link `active`) | `{call_id}` |
| Nghe máy | `POST /api/v1/calls/{call_id}/answer` | không | `{call_id, media_url, media_token\|null}` |
| Từ chối (khi đang chuông) | `POST /api/v1/calls/{call_id}/reject` | không | `CallData` |
| Cúp / huỷ | `POST /api/v1/calls/{call_id}/hangup` | không | `CallData` |
| Xem / đồng bộ | `GET /api/v1/calls/{call_id}` | | `CallData` |

```json
{ "status": "ok", "data": { "call_id": "1886c84f-9436-4edd-a417-24cc1aa66c3c", "media_url": "wss://api.visioncare-host.uk/v2/call",
  "media_token": "s3cr3t-one-time-token" } }
```

```json
{ "status": "ok", "data": { "call_id": "1886c84f-9436-4edd-a417-24cc1aa66c3c", "direction": "to_relative", "state": "active",
  "end_reason": null, "emergency": false, "peer_display_name": "Cô Sáu", "media_url": "wss://api.visioncare-host.uk/v2/call",
  "media_token": null, "created_at": "2026-10-20T07:10:01Z", "answered_at": "2026-10-20T07:10:03Z", "ended_at": null } }
```

- `media_token` chỉ giao **MỘT lần** (lần nghe đầu tiên; gọi `answer` lặp lại trả `null`), dùng một lần ở `WS /v2/call`, hết hạn sau khi hết chuông.
  Cuộc gọi `to_glasses`: token tới qua FCM `call_answered` (hoặc `GET` trong vòng 60 s sau khi kính nghe). Đừng ghi token ra log.
- `peer_display_name`: với người thân là tên chủ kính (tên tài khoản của họ); `end_reason` ví dụ `cancelled|rejected|hangup|missed|timeout|revoked|max_duration|glasses_offline|busy|app_hangup|glasses_hangup`.
- Gọi lặp khi cuộc `ringing` tới kính còn hiệu lực trả lại cùng `call_id` (không đổ chuông thêm).
- Lỗi: `404 CALL_NOT_FOUND` (không phải bên liên quan), `403 CALL_NOT_ALLOWED` (không còn link `active`), `409 CALL_STATE_CONFLICT`
  (nghe/cúp cuộc đã kết thúc hoặc quá hạn chuông).
- Người thân không có điện thoại đã đăng ký: cuộc gọi vào thẳng `missed` trong ~1 s.
- Mọi cuộc gọi `active` quá 2 giờ bị server đóng (`max_duration`).

### 7.3. Âm thanh: `WS /v2/call?token=<media_token>`

App nối vào `media_url` + `?token=<media_token>` **ngay sau khi nghe** (`answer` / FCM `call_answered`). Quá 15 s sau khi cuộc gọi `active` mà chưa nối thì
server cúp (`media_timeout`). Token sai/đã dùng/hết hạn: server `accept` rồi đóng mã **4401**.

| Hướng | Khung | Nội dung |
|---|---|---|
| app → server | binary | PCM16 little-endian, **16 kHz, mono, khung 20 ms = 640 byte**, gửi đều đặn theo thời gian thực (không gộp, không bỏ). Khung dài hơn 16 KB bị bỏ. |
| server → app | binary | cùng định dạng (tiếng người trên kính), 640 byte/khung |
| app → server | text | `{"t":"hangup"}` cúp; `{"t":"mute","on":true}` báo app tắt mic (chỉ thông tin; đơn giản nhất là ngừng gửi khung) |
| server → app | text | `{"t":"state","state":"ringing"\|"active"\|"ended","reason"?:str}`; sau `ended` server đóng socket (1000) |

- Server đệm chống giật 60 ms mỗi hướng, bỏ khung trễ quá 200 ms; app KHÔNG cần tự đệm phía gửi, phía nhận nên có đệm nhỏ (~60 ms) và phát đều.
- Kính có thể không khử vọng tốt: app nên bật tiêu chuẩn `VOICE_COMMUNICATION` (AEC/NS của hệ điều hành) khi thu mic.
- Cúp từ app: gửi `{"t":"hangup"}` rồi `POST /calls/{id}/hangup` (idempotent, an toàn gọi cả hai). Mất mạng/đóng socket ≈ cúp (`peer_disconnect`).
- Độ trễ một chiều đo local (không gồm mạng): kính→app ≈ 40–60 ms, app→kính ≈ 80 ms (xem `visioncare-host-demo/docs/E2E_FULL_FLOW.md`); mục tiêu qua Cloudflare ≤ 400 ms.

### 7.4. Nhắn tin

| Việc | Endpoint | Body | `data` |
|---|---|---|---|
| Gửi tới kính | `POST /api/v1/messages` | `{owner_user_id, text (1-1000), request_id (UUID)}` | `{message_id, created_at}` |
| Lịch sử | `GET /api/v1/messages?with=<user_id>&before=<ISO>&limit=50` | | `{messages:[{id, direction, from_me, text, created_at, read_at}]}` mới → cũ |

- `request_id` do app sinh cho mỗi tin; gửi lại cùng `request_id` + cùng người gửi là idempotent (không nhắn đôi); cùng id khác người gửi: `409 REQUEST_ID_CONFLICT`.
- `read_at` (tin gửi tới kính): lúc kính ĐỌC cho người dùng nghe; app hiện "đã nghe". Kính đang bận (trong lượt/cuộc gọi) sẽ xếp hàng rồi đọc, không mất.
- Tin từ kính tới người thân đến bằng FCM `message_in` (bên dưới); lịch sử có cả hai chiều (`direction`: `to_glasses|to_relative`, `from_me` theo người gọi API).
- Lỗi: `403 MESSAGE_NOT_ALLOWED` (không có link `active`), `400 INVALID_REQUEST` (rỗng/toàn khoảng trắng/>1000 ký tự).
- Không có SMS: tin chỉ hiện trong app.

### 7.5. Khẩn cấp

Người dùng nói "cấp cứu / gọi người thân giúp tôi" → server nhắn **mọi** người thân `emergency:true` một tin `KHẨN CẤP: <tên chủ kính> cần giúp. Vị trí: <link bản đồ>`
(không kèm vị trí nếu điện thoại chủ kính không trả lời trong 5 s) và gọi lần lượt theo `priority`, mỗi người đổ chuông tới hết chuông, hết danh sách thì
lặp lại, **tối đa 3 vòng**. Người đầu tiên nghe máy thì nối âm thanh như cuộc gọi thường; chủ kính bấm nút khi đang chuông thì dừng cả chuỗi.
Với app: **không có endpoint riêng**, chỉ khác ở dữ liệu FCM: `call_incoming.emergency == "true"` và tin `message_in` có `text` bắt đầu bằng `KHẨN CẤP`.
App nên dùng kênh thông báo ưu tiên cao, đổ chuông to, hiện toàn màn hình, cho nghe/từ chối như cuộc gọi thường (cùng API mục 7.2).

### 7.6. FCM

Toàn bộ là **data message** (không có khối `notification`), `android.priority=high`, `ttl=30s`, mọi giá trị là **chuỗi**; trường `type` quyết định loại.
Điện thoại offline quá TTL thì mất thông điệp: khi mở app/đổ chuông trễ hãy `GET /api/v1/calls/{call_id}` để đồng bộ trạng thái.

```json
{ "type": "call_incoming", "call_id": "1886c84f-9436-4edd-a417-24cc1aa66c3c", "from_display_name": "Cô Sáu", "emergency": "false" }
```
```json
{ "type": "call_answered", "call_id": "3ec6b46b-8e02-4e49-bc45-8236c6176f76", "media_url": "wss://api.visioncare-host.uk/v2/call", "media_token": "s3cr3t-one-time-token" }
```
```json
{ "type": "call_ended", "call_id": "1886c84f-9436-4edd-a417-24cc1aa66c3c", "reason": "glasses_hangup" }
```
```json
{ "type": "message_in", "message_id": "7edb532f-d951-4fb2-abd1-b22b869aeaf9", "from_display_name": "Cô Sáu", "text": "Con sẽ về muộn" }
```

| `type` | Khi nào | Việc app làm |
|---|---|---|
| `call_incoming` | kính gọi người thân (kể cả khẩn cấp, `emergency:"true"`) | hiện màn hình cuộc gọi đến (chuông, Nghe/Từ chối) |
| `call_answered` | người thân gọi kính và **kính đã bấm nghe** | nối `WS /v2/call` bằng `media_url`+`media_token` |
| `call_ended` | cuộc gọi kết thúc ở phía kính/server/hết chuông (`reason`) | tắt chuông / đóng màn hình / đóng media WS |
| `message_in` | kính nhắn người thân, hoặc tin KHẨN CẤP | thông báo + thêm vào hội thoại |

### 7.7. Máy trạng thái màn hình cuộc gọi

```text
              FCM call_incoming                  bấm Nghe: POST /answer → mở WS
  (rảnh) ───────────────────► ĐỔ CHUÔNG ─────────────────────────────► ĐANG NÓI ──► (rảnh)
                                │  Từ chối: POST /reject / hết giờ / FCM call_ended │  Cúp: WS {"t":"hangup"} + POST /hangup
                                └────────────────────────────────────────────────►──┘  hoặc server {"t":"state","state":"ended"}
  (rảnh) ── bấm Gọi kính: POST /calls ─► ĐANG GỌI ── FCM call_answered → mở WS ─► ĐANG NÓI
                                         │  Huỷ: POST /hangup · FCM call_ended (missed/rejected/...)
```

- Đang nói: đồng hồ, nút tắt mic (ngừng gửi khung / `{"t":"mute"}`), nút loa ngoài, nút cúp. Khi nhận `state:"ended"` hoặc socket đóng: dừng thu/phát, hiện lý do (`reason`).
- Bấm Gọi kính khi kính đang bận hoặc offline: cuộc gọi kết thúc nhanh qua FCM `call_ended` (`busy`/`glasses_offline`), hiển thị "Kính đang bận / không kết nối".

### 7.8. Quyền và cấu hình app

| Quyền | Để làm gì | Bắt buộc |
|---|---|---|
| Micro (`RECORD_AUDIO`) | thu giọng khi nói chuyện | có |
| Thông báo (`POST_NOTIFICATIONS`), thông báo toàn màn hình | nhận FCM, hiện cuộc gọi đến khi khoá máy | có |
| Bỏ tối ưu pin / chạy nền | FCM priority cao tới ngay | nên có |
| Camera | — | **không cần** |
| `CALL_PHONE` / `READ_PHONE_STATE` / SMS | — (không gọi qua mạng di động) | **không cần** cho tính năng này |

Các action cũ của mục 4 (`contact_call`, `emergency_call`, `call_answer`...) là đường gọi điện bằng SIM của Android; tính năng ở mục 7 độc lập với chúng.

### 7.9. Thử không cần app thật

`server/scripts/relative_sim.py` đóng vai người thân (đăng ký + OTP + chấp nhận lời mời, nhận FCM qua `DELIVERY_TRANSPORT=webhook`, nghe/từ chối/bỏ qua theo
kịch bản, mở `WS /v2/call`, phát PCM16 và ghi tiếng nhận ra `rx_*.wav`). Chạy cùng kính giả lập bằng `visioncare-host-demo/tools/e2e_calls.py`
(xem mục 8 và `docs/E2E_FULL_FLOW.md` ở repo server kính).

## 8. Kiểm thử không cần kính

**Điện thoại giả** `server/scripts/phone_sim.py`: nhận webhook như FCM và báo kết quả đúng cấu trúc app Android
dựng, có độ trễ ngẫu nhiên theo seed.

1. Chạy app server với `DELIVERY_TRANSPORT=webhook` và `DELIVERY_WEBHOOK_URL=http://127.0.0.1:<port>/push`.
   Transport webhook (`WebhookDeliveryAdapter`) POST đúng `_command_data` (kể cả `maps_url`) kèm header
   `X-Push-Token`; 2xx = sent, lỗi mạng/5xx/429 = tạm thời, 4xx khác = vĩnh viễn.
2. Đặt `DEVICE_API_TOKEN` vào môi trường (hoặc `--env-file`, mặc định `SeeVia_system/.env`), rồi:

   ```bash
   python server/scripts/phone_sim.py --app-url http://127.0.0.1:18001 --port 18002 \
       --user-id e2e-user --glasses-id glasses-e2e [--scenario scenario.json] [--capture-dir out/]
   ```

   Script tự `register` (với `push_token="sim"`) và `glasses/link`, rồi mở `POST /push`.
3. Kịch bản `--scenario` (JSON, mọi khoá tuỳ chọn): `contacts`, `delay_min`, `delay_max`, `seed`,
   `emergency_delay`, `fail_actions` (ví dụ `{"music_play":"PLAYBACK_FAILED"}`), `emergency_contact`,
   `incoming_call`, `geocode_fails`, `capabilities`.
4. Log: mọi lệnh nhận và report gửi đi ghi vào `phone_sim_log.jsonl`. `--capture-dir` lưu payload thô
   `<action>.json` để làm fixture (chính là `server/tests/fixtures/server_payloads/`).

Sau đó giả làm server kính gọi `POST /api/v1/service/<chức năng>` (xem `external-client-api.md`) và theo dõi
`GET /api/v1/requests/{request_id}`. Với app thật, đặt `DELIVERY_TRANSPORT=fcm` (cần `FCM_PROJECT_ID` và
`GOOGLE_APPLICATION_CREDENTIALS` ngoài repo).

Lưu ý: `phone_sim.py` mô phỏng `navigation_start` ở mức báo kết quả (không có thông báo thật), nên không kiểm
được phần thông báo chạm-để-mở của mục 4.1; phần đó cần máy thật hoặc emulator.

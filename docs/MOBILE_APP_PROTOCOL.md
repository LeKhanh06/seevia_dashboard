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

(bổ sung khi plan 2026-10-19-glasses-calls-messages xong)

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

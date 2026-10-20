# App SeeVia — giao tiếp tối thiểu với kính: thiết kế

Ngày 2026-10-20. Repo AB (`app-demo-backend`), app ở `mobile/` (Kotlin + Compose, package `com.youreyes.app`).
Đây là **đợt 1** của spec lớn `2026-10-20-seevia-mobile-app-design.md`; phần còn lại của spec đó làm ở đợt sau.
Nguồn rà soát: `SeeVia_system/docs/2026-10-20-ra-soat-giao-tiep-vatcan-ocr-benchmark.md` (mục A).
Hợp đồng: `docs/MOBILE_APP_PROTOCOL.md` (mục 3–5 lệnh, 4.1 chỉ đường, 7 gọi/nhắn).

## Vì sao

Server (AB + VH) đã xong phần gọi/nhắn qua internet giữa kính và người thân, nhưng app chưa có code nào cho mục 7:
FCM `type=call_incoming|call_answered|call_ended|message_in` bị `FcmPushReceiver` bỏ qua. Kính gọi người thân → không đổ chuông.
Đường lệnh kính → điện thoại chạy được nhưng có lỗi làm lệnh gấp bị trễ/hết hạn. Người dùng muốn **trước hết bảo đảm chức năng giao tiếp chạy được**.

## Mục tiêu (đợt 1)

1. **Người thân ↔ kính qua app**, đúng hợp đồng mục 7:
   - Danh sách người thân (`GET /care/links`), **chấp nhận lời mời** (người thân), **mời** (chủ kính: số điện thoại, tên hiển thị, alias, khẩn cấp, ưu tiên), thu hồi.
   - **Cuộc gọi đến từ kính** (`call_incoming`, cả khẩn cấp): đổ chuông **cả khi máy khoá/app đã tắt**, Nghe/Từ chối, tự tắt khi `call_ended`.
   - **Gọi tới kính** (`POST /calls`), chờ `call_answered`, huỷ được; hiện lý do kết thúc (bận, kính không kết nối, nhỡ…).
   - **Đang nói:** âm thanh hai chiều qua `WS /v2/call` (PCM16LE 16 kHz mono, khung 20 ms = 640 B), tắt mic, loa ngoài, cúp, đồng hồ.
   - **Nhắn tin**: hội thoại 1-1 với từng người thân (lịch sử, gửi, "đã nghe" theo `read_at`), thông báo khi có `message_in`; tin `KHẨN CẤP` nổi bật.
2. **Sửa đường lệnh** (FCM có `request_id`):
   - Lệnh gấp không phải chờ lệnh chậm (hai làn, xem "How").
   - Đọc `expires_at`; lệnh đã hết hạn không chạy.
   - Report gửi lỗi được gửi lại khi có mạng, không chỉ khi có lệnh mới; lỗi 4xx vĩnh viễn thì bỏ ngay.
   - Không bị hệ thống giết giữa lệnh vì hết giờ `shortService`.
   - `navigation_start` theo mục 4.1 (thông báo "chạm để mở Google Maps", đọc `maps_url`).
3. **Build được APK debug trên máy này** và test JVM chạy xanh (dự án chưa build lần nào ở đây).

## Không làm (đợt này)

- Gọi/nhắn **điện thoại ↔ điện thoại** (cần thêm server), danh bạ hai chiều không cần kính, chọn từ danh bạ máy.
- Đổi tên/nhận diện SeeVia, ẩn màn hình không có server, màn hình debug đầy đủ (echo, RTT) — đợt 2.
- Video, gửi ảnh/file/tin thoại, gọi nhóm. Sửa VH/kính. Sửa server AB (trừ khi buộc phải khớp — nêu rõ).
- Vật cản, OCR, benchmark AI (để sau).

## Điều sẽ khiến việc này hỏng dù "chạy được"

- Cuộc gọi đến không đổ chuông khi máy khoá / app bị vuốt tắt.
- Lộ `media_token`, access token, Device token, số điện thoại đầy đủ trong log; commit `google-services.json` hay khoá Firebase.
- Phá đường lệnh cũ của chủ kính (nhạc, Grab, chỉ đường, vị trí, SIM khẩn cấp) hoặc thay đổi hợp đồng public.
- Tiếng vọng to/giật liên tục đến mức không nói chuyện được (phải dùng AEC + jitter buffer).
- `emergency_call` (dự phòng SIM) bị kẹt sau lệnh khác.

## Ràng buộc

- Theo `AB/claude.md` mục Android: FCM receiver không chạy nghiệp vụ; xử lý trùng, process death, permission denied rõ ràng;
  không log dữ liệu nhạy cảm dạng thô. Không đổi hợp đồng nếu không cần; nếu đổi thì cập nhật `MOBILE_APP_PROTOCOL.md` + contracts.
- Xác thực mục 7: `Authorization: Bearer <access_token>` phiên người dùng (`AppConfig.KEY_ACCESS_TOKEN`), **không** phải Device token.
  Điện thoại phải `POST /api/v1/device/register` với `user_id = public_user_id` để nhận FCM (luồng đăng nhập hiện có đã ghi `KEY_USER_ID`).
- Máy này: JDK 21 `~/jdk-21.0.12.1+1`, SDK `~/android-sdk`, không có emulator (không KVM). Kiểm bằng unit test JVM + script giả lập server
  (`server/scripts/relative_sim.py`, `phone_sim.py`, `visioncare-host-demo/tools/e2e_calls.py`). Thử máy thật là việc người dùng.
- Tiết kiệm token: subagent sonnet, gộp task, review bảo mật + 1 review cuối (memory `seevia-toi-uu-token-dieu-phoi`).
- UI mới (màn hình cuộc gọi, người thân, hội thoại) đi qua skill `ui-ux-pro-max`; giữ theme hiện tại của app ở đợt này
  (đổi nhận diện SeeVia là đợt 2), nhưng nút cuộc gọi phải to, tương phản cao, có `contentDescription`.
- Hai file Firebase không commit/copy/in.

## "How" controller đã chọn (người dùng chưa phản đối, sửa được)

- **Đổ chuông bằng `ConnectionService` self-managed** (`TelecomManager.addNewIncomingCall`, `PhoneAccount.CAPABILITY_SELF_MANAGED`),
  kèm thông báo `CATEGORY_CALL` + full-screen intent vào `CallActivity`. Lý do: Android coi là cuộc gọi thật → đổ chuông khi khoá máy,
  tai nghe Bluetooth nghe/cúp được, các hãng ít chặn hơn. Cần quyền `MANAGE_OWN_CALLS` (normal permission).
  Nếu hệ thống từ chối (`onCreateIncomingConnectionFailed`) thì lùi về thông báo full-screen thường. Khẩn cấp: kênh thông báo riêng, ưu tiên cao.
- **Receiver tách đường:** FCM có `type` → bộ định tuyến gọi/nhắn; có `request_id` → đường lệnh cũ, không đổi hành vi.
- **Âm thanh:** `AudioRecord(VOICE_COMMUNICATION)` + bật `AcousticEchoCanceler`/`NoiseSuppressor` nếu có; `AudioTrack` (`USAGE_VOICE_COMMUNICATION`);
  jitter buffer phía nhận ~60 ms (theo `CALL_JITTER_MS` của VH), thiếu khung thì phát im lặng; gửi đều mỗi 20 ms, không gộp.
  Foreground service loại `phoneCall` (+ `microphone`) suốt cuộc gọi.
- **WebSocket:** thêm thư viện **OkHttp** (dự án chưa có client WS). **Report retry:** thêm **WorkManager** (ràng buộc có mạng, backoff).
  Hai phụ thuộc Gradle mới này: **người dùng đã đồng ý 2026-10-20**.
- **Đồng bộ trạng thái:** mở màn hình cuộc gọi nào cũng `GET /calls/{id}` trước (FCM TTL 30 s có thể tới trễ); cuộc đã `ended/missed` thì không đổ chuông.
- **Hàng đợi lệnh hai làn:** làn nhanh (`emergency_call`, `call_answer`, `call_reject`, `location_get`, `capabilities_get`) và làn chậm (còn lại),
  mỗi làn một luồng. Chống trùng `request_id` giữ nguyên (SQLite, trước khi chạy).
- **Hết hạn:** quá `expires_at` thì không chạy handler, ghi `failed / COMMAND_EXPIRED` cục bộ và gửi report một lần (server trả 409 thì bỏ).
  ❓ mã `COMMAND_EXPIRED` là mã mới phía app; server chấp nhận mọi `error.code` hay không — builder kiểm `schemas/device.py`; nếu không thì chỉ ghi log, không gửi.
- **`onTimeout` của `shortService`:** kết thúc lệnh đang chạy bằng `failed / EXECUTION_FAILED` rồi `stopSelf`.

## Nguyên tắc người dùng chốt (2026-10-20): kính = âm thanh, điện thoại = chữ

Người đeo kính là người khiếm thị: **mọi phản hồi tới họ qua kính phải bằng âm thanh** (câu nói/earcon), không dựa vào chữ hay thông báo
trên màn hình. Chữ/thông báo hiển thị chỉ dành cho người dùng điện thoại (người thân, hoặc người sáng mắt cài đặt hộ).
Hệ quả cho đợt này:

- Mọi kết quả app báo về (report) phải để VH nói ra được một câu rõ nghĩa. Mã mới `COMMAND_EXPIRED` → thêm **một dòng** vào
  `VH/handlers/result_speech.py::_ERROR_SPEECH`: "Điện thoại nhận lệnh quá muộn nên chưa làm. Bạn nói lại giúp tôi nhé." (thay đổi VH duy nhất, kèm test).
- Không thêm câu nói nào bảo người khiếm thị "xem/bấm thông báo trên điện thoại". Các câu hiện có của VH đang làm vậy
  (`_TIMEOUT_MESSAGE`, `navigation_start` "bấm vào thông báo") — **ghi nhận, không sửa đợt này**, đưa vào danh sách bàn tiếp
  (`SeeVia_system/docs/2026-10-20-ra-soat-...md`) vì đụng tới thiết kế chỉ đường đã chốt.
- Trên điện thoại: chữ tiếng Việt dễ hiểu cho mọi trạng thái/lỗi; màn hình cuộc gọi vẫn có `contentDescription` đầy đủ cho TalkBack
  (chủ kính có thể tự mở app bằng TalkBack).

## Đã làm rõ sau builder check (controller quyết, theo những gì người dùng đã chốt)

- **Vai:** một app, không chọn vai (người dùng đã chốt). Một tài khoản có thể vừa là chủ kính vừa là người thân. Màn "Người thân" hiện
  **cả hai phần theo `role` trong `GET /care/links`**: link `role=owner` (mình là chủ kính) có nút sửa/thu hồi, form "Mời" luôn có;
  link `role=relative` có nút "Gọi kính"/"Nhắn" tới chủ kính đó và nút "Chấp nhận" khi `pending`. Nhiều chủ kính → mỗi dòng một nút gọi, không cần bộ chọn riêng.
- **Mã `COMMAND_EXPIRED`:** server nhận `error.code` tự do → gửi report bình thường; 409 thì bỏ.
- **Tương tác với đường SIM cũ:** cuộc gọi VoIP self-managed **không được** kích hoạt `IncomingCallMonitor` (6.1), `call_answer/call_reject`
  hay `contact_call`. Builder phải kiểm (self-managed không đổi trạng thái `TelephonyManager`, nhưng vẫn cần test/guard: monitor bỏ qua khi
  đang có cuộc VoIP của app). Có cuộc gọi SIM thật đang diễn ra khi VoIP đổ chuông → vẫn đổ chuông (Telecom xử lý giữ/chờ). Cuộc gọi VoIP giữ
  audio focus `AUDIOFOCUS_GAIN_TRANSIENT`; nhạc Spotify tự tạm dừng.
- **Phiên hết hạn (401):** API gọi/nhắn trả 401 → xoá access token, hiện thông báo "Phiên đăng nhập hết hạn — mở app để đăng nhập lại",
  mở app thì vào màn đăng nhập. Không làm refresh token ở đợt này. `answer` lỗi khi đang chuông → dừng chuông, hiện lý do, ghi nhật ký.
- **Nghe sau `media_timeout`/cuộc đã kết thúc:** `answer` trả 409 → hiện "Cuộc gọi đã kết thúc". ConnectionService bị từ chối → lùi về
  thông báo full-screen; chấp nhận rủi ro một số máy không đổ chuông khi khoá — ghi vào danh sách thử tay trên máy thật.
- **Lịch sử tin nhắn:** chỉ lấy từ server (không cache Room/SQLite đợt này); tin mới từ FCM chèn vào danh sách đang mở.

## Để builder quyết

Cấu trúc package/màn hình, tên route; lưu lịch sử tin nhắn (chỉ lấy từ server hay cache cục bộ); cách tách test JVM (logic thuần: router FCM,
máy trạng thái cuộc gọi, jitter buffer, chọn làn, hết hạn) so với phần chỉ thử tay (âm thanh, đổ chuông); số lần/khoảng backoff cụ thể.

## Tiêu chí chấp nhận

- `./gradlew test assembleDebug` xanh trên máy này; APK ở đường dẫn rõ ràng.
- Test JVM cho: router FCM (4 `type` + lệnh cũ + thiếu khoá), máy trạng thái cuộc gọi 7.7 (mọi chuyển trạng thái + FCM trễ/trùng),
  jitter buffer (thiếu/thừa/đảo khung), chọn làn, bỏ lệnh hết hạn, flusher bỏ 4xx.
- AB test hiện có vẫn xanh (420 pass) nếu có đụng server.
- Danh sách thử tay trên máy thật cho người dùng: đổ chuông khi khoá máy/app tắt, chất lượng tiếng, khẩn cấp, Bluetooth.
- Cập nhật mục "Nhật ký đã làm" trong `SeeVia_system/docs/2026-10-20-ra-soat-giao-tiep-vatcan-ocr-benchmark.md`.

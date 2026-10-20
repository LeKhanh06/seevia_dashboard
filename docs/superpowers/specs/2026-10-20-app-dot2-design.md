# App SeeVia — đợt 2: thiết kế

**Trạng thái: người dùng ĐÃ DUYỆT 2026-10-20 (kể cả "kính offline/bận → đổ chuông điện thoại"), nhưng CHƯA thực thi — chờ người dùng bảo bắt đầu.**
Bước tiếp khi được bảo: `superpowers:writing-plans` từ spec này.

Ngày 2026-10-20. Repo AB (`app-demo-backend`): app `mobile/`, server `server/`. Server kính VH = `visioncare-host-demo`.
Đợt 2 = phần còn lại của spec lớn `2026-10-20-seevia-mobile-app-design.md` (đọc mục "Vì sao", "Người dùng đã chốt", "Mục tiêu", "Không làm",
"Điều sẽ khiến việc này hỏng" ở đó — vẫn áp dụng). Đợt 1 (`2026-10-20-app-giao-tiep-toi-thieu-design.md`) đã cài xong trên nhánh `app-giao-tiep`:
gọi/nhắn kính ↔ người thân, màn Người thân, máy trạng thái cuộc gọi, âm thanh WS, Telecom self-managed, sửa đường lệnh.

## Phạm vi đợt 2

1. **Liên hệ hai chiều không cần kính** ("bạn bè"): mời bằng số điện thoại (nhập tay hoặc **chọn từ danh bạ máy**), bên kia chấp nhận → hai bên là liên hệ của nhau.
2. **Gọi điện thoại ↔ điện thoại** qua internet, chỉ thoại; **nhắn tin điện thoại ↔ điện thoại**.
3. **Sửa alias/ưu tiên/khẩn cấp** của link người thân (PATCH đã có ở server, app chưa có).
4. **Đổi tên/nhận diện Your Eyes → SeeVia** ở mọi chỗ người dùng thấy; giữ `applicationId` `com.youreyes.app`.
5. **Ẩn màn hình không có server** (cờ tính năng, giữ code).
6. **Màn debug** (chỉ bản debug hoặc sau khi bật devmode).

Không làm: video, nhóm, ảnh/file/tin thoại; iOS; Play Store; tự triển khai server.

## Người dùng đã chốt (2026-10-20, đợt 2)

- **Âm thanh điện thoại ↔ điện thoại đi qua AB**: AB thêm cầu WebSocket âm thanh, cùng định dạng và JSON điều khiển như `WS /v2/call` của VH,
  để app dùng lại đúng `CallMediaSocket`/`CallAudioEngine`/máy trạng thái đợt 1. Lý do: kính/VH hỏng thì người thân vẫn gọi nhau được.
- **Gọi tới người đeo kính luôn đổ chuông trên kính**, bất kể người gọi là người thân hay bạn bè (người đeo kính có thể không cầm điện thoại).
- **Liên hệ bạn bè KHÔNG BAO GIỜ vào chuỗi gọi khẩn cấp.** Chỉ người thân do chủ kính mời và bật `emergency` mới ở đó (như hiện tại).
- **Mình chỉ viết code + migration + test + hướng dẫn triển khai**; người dùng tự chạy migration, mở WebSocket qua Cloudflare, khởi động lại AB trên server thật.

## Quyết định của controller (sửa được)

- **Mô hình liên hệ:** thêm cột `care_links.kind` = `glasses` (hành vi hiện tại, owner→relative, có alias/emergency/priority) | `peer` (đối xứng sau khi
  chấp nhận; mỗi bên một tên gọi riêng `alias_by_owner`, `alias_by_relative` — owner ở đây = người mời; không có emergency/priority).
  Migration: mọi dòng cũ `kind=glasses`. Truy vấn khẩn cấp của AB/VH lọc `kind=glasses AND emergency`. Một cặp người có thể có cả hai loại link.
- **Định tuyến cuộc gọi** (`POST /api/v1/calls` mở rộng nhận `{peer_user_id}`; giữ `{owner_user_id}` cũ):
  - Người nhận có kính đã ghép (bất kể loại link) → đi đường kính hiện có (`to_glasses`), VH đọc tên người gọi từ `peer_display_name`/alias.
  - **Kính offline/bận → lùi về đổ chuông điện thoại người nhận** (phone↔phone) thay vì kết thúc ngay (người dùng đồng ý 2026-10-20).
  - Người nhận không có kính → `phone_to_phone`: cột `calls.glasses_device_id` thành nullable, thêm `caller_user_id`, `callee_user_id`;
    FCM `call_incoming` tới điện thoại người nhận (cùng payload đợt 1 + `peer_user_id`), nghe → `POST /calls/{id}/answer` trả `media_url` của AB.
  - Cuộc gọi chỉ được phép khi có link `active` (bất kỳ loại) giữa hai người.
- **Cầu âm thanh AB:** `WS /api/v1/calls/media?token=<media_token>` trên AB, relay khung 640 B, đệm chống giật 60 ms phía server như VH,
  `media_timeout` 15 s, `max_duration` 2 giờ, đóng 4401 cho token sai; registry trong bộ nhớ → **AB phải chạy 1 worker** (ghi rõ trong hướng dẫn triển khai).
  Token một lần như VH. Không ghi âm, không log khung.
- **Nhắn tin:** thêm `sender_user_id`, `recipient_user_id` (nullable) + direction `peer`; endpoint mới `POST /api/v1/messages/peer {peer_user_id, text, request_id}`,
  `GET /api/v1/messages/peer?with=<user_id>`; `POST /api/v1/messages/peer/read {with}` đặt `read_at` khi người nhận mở hội thoại. Giữ nguyên endpoint/VH service API cũ.
  Người nhận có kính: tin bạn bè gửi tới người đeo kính → **kính đọc to** như tin người thân (nguyên tắc kính = âm thanh).
- **Gọi bằng giọng nói từ kính:** VH khớp tên trong cả link `peer` (alias của chủ kính đặt) để "gọi Nam" gọi được bạn bè; khẩn cấp vẫn chỉ `glasses+emergency`.
  Đây là thay đổi VH nhỏ (nguồn tên + hotword STT), kèm test.
- **Chọn từ danh bạ:** `ACTION_PICK` (`ContactsContract.CommonDataKinds.Phone`), không xin `READ_CONTACTS`, không tải danh bạ lên server; chỉ chép số đã chọn vào form mời.
- **Màn debug:** URL server (xem/sửa), kiểm server sống, đăng nhập, FCM token đã đăng ký; nhật ký sự kiện gần nhất (FCM, API, trạng thái cuộc gọi, WS, lỗi —
  đã che token/số/nội dung tin) có nút sao chép; **gọi thử echo** qua `WS /api/v1/debug/echo` trên AB (bật bằng cờ cấu hình `DEBUG_ECHO_ENABLED`, xác thực Bearer,
  trả lại khung, trả `{"t":"pong"}` cho `{"t":"ping","ts":..}`) để đo RTT.
- **Ẩn màn hình:** builder liệt kê màn nào không có endpoint server (album, cộng đồng, họp, dịch… — kiểm từng màn), ẩn bằng cờ trong một `FeatureFlags` object; giữ code.
- **Nhận diện:** navy `#041466` chủ đạo, Montserrat (chữ) + Pinyon Script (chỉ wordmark), logo/icon từ `SeeVia_system/ui/logo/`; UI qua skill `ui-ux-pro-max`.
  Nếu Montserrat hiển thị dấu tiếng Việt kém ở cỡ nhỏ thì giữ Be Vietnam Pro cho thân chữ (builder quyết, ghi lại).

## Ràng buộc

Như đợt 1 (Global Constraints trong plan đợt 1): không lộ token/số/nội dung tin; hai file Firebase bí mật; không thêm thư viện ngoài danh sách đã duyệt
(OkHttp, WorkManager) — server AB: FastAPI đã hỗ trợ WebSocket, không thêm gói Python; migration Alembic; test AB trên Postgres thật (cổng 55432);
cập nhật `MOBILE_APP_PROTOCOL.md` + `contracts/` khi đổi API; kính = âm thanh.

## Tiêu chí chấp nhận

- AB: test mới cho link `peer`, định tuyến cuộc gọi (có kính / kính offline → điện thoại / không kính), cầu WS (relay, 4401, media_timeout), tin `peer`,
  echo; toàn bộ test AB cũ vẫn xanh. `relative_sim.py` mở rộng để giả lập cuộc gọi phone↔phone (hai user) chạy được local.
- VH: khớp tên link `peer`, khẩn cấp không đổi; pytest xanh.
- App: `test assembleDebug lint` xanh, lint 0 error; test JVM cho phần logic mới.
- Hướng dẫn triển khai AB (migration, 1 worker, Cloudflare WS) + bổ sung kịch bản thử tay đợt 2.

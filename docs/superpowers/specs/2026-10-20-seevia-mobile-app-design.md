# App điện thoại SeeVia: gọi/nhắn qua internet, danh bạ, debug — thiết kế

Ngày 2026-10-20. Repo: `app-demo-backend` (AB) — app ở `mobile/` (Kotlin + Compose, hiện tên "Your Eyes"), server ở `server/`.
Server kính (VH) = `visioncare-host-demo`. Hợp đồng hiện có: `docs/MOBILE_APP_PROTOCOL.md` (mục 7 gọi/nhắn với kính).

## Vì sao

Người đeo kính (khiếm thị) gọi và nghe hoàn toàn trên kính. Người thân phải liên lạc được với họ bằng app trên điện thoại,
kiểu Messenger. Người dùng muốn **tải APK về là dùng được thật trên điện thoại**, có công cụ debug để tự kiểm thử, không phải
tự viết app nữa (thay quyết định cũ "app người dùng tự làm, không sửa `mobile/`").

## Người dùng đã chốt

- **Một app, ai cũng là user bình thường**, không chọn vai. Đăng ký bằng số điện thoại (luồng auth/OTP hiện có).
- **Danh bạ như bình thường:** thêm người bằng số điện thoại; bên kia chấp nhận thì thành liên hệ của nhau.
- **Ba kiểu gọi, chỉ thoại qua internet, KHÔNG video, KHÔNG SIM:** kính → điện thoại, điện thoại → kính, **điện thoại ↔ điện thoại**
  (hai người không đeo kính gọi nhau được).
- Người đeo kính gọi bằng giọng nói trên kính như hiện tại (VH, đã xong). Người kia gọi người đeo kính bằng app.
- **Khẩn cấp giữ đường SIM** (SMS `[SOS]` + gọi qua mạng di động trên điện thoại chủ kính) làm dự phòng khi đường internet không dùng được.
- **Đổi tên Your Eyes → SeeVia** ở mọi chỗ người dùng thấy (tên app, logo, icon, chữ trong app). **Giữ** `applicationId`/package
  `com.youreyes.app` để dùng được `google-services.json` hiện có (Firebase project `your-537d3`).
- **Màn hình không có server phía sau** (album, cộng đồng, họp, dịch… — danh sách cụ thể do builder xác định) thì **ẩn**,
  giữ code để bật lại sau; không xoá.
- Giao diện theo code/chức năng hiện tại và nhận diện SeeVia; mọi thiết kế UI đi qua skill `ui-ux-pro-max`.
- FCM dùng Firebase sẵn có (`mobile/app/google-services.json`, đã đặt, git-ignored; khoá server ở
  `~/.config/seevia/firebase-adminsdk.json`, `.env` của `SeeVia_system/` trỏ tới). Hai file này **không** được commit, copy, in ra log.

## Mục tiêu

1. APK debug cài được trên Android 7+ (minSdk 24), mở lên đăng ký/đăng nhập, dùng thật với server.
2. Danh bạ: mời theo số (có chọn từ danh bạ máy), chấp nhận/từ chối lời mời, đặt tên gọi (alias như "mẹ", "anh Nam" — kính dùng để khớp lệnh
   giọng nói), thu hồi. Hiện liên hệ nào có kính.
3. Gọi thoại: gọi đi, cuộc gọi đến đổ chuông **cả khi app tắt/máy khoá** (FCM priority cao + thông báo toàn màn hình), nghe/từ chối/cúp,
   loa ngoài/tắt mic. Liên hệ có kính → cuộc gọi tới kính (hợp đồng mục 7 hiện có); không có kính → cuộc gọi tới điện thoại người đó.
4. Nhắn tin: hội thoại 1-1 với liên hệ, lịch sử hai chiều, thông báo tin mới. Tin tới người đeo kính do kính đọc to (đã có).
5. Phần điện thoại chủ kính hiện có (nhận lệnh FCM từ kính: nhạc, bản đồ, Grab, vị trí, SIM khẩn cấp…, ghép kính, cấp Wi-Fi BLE) giữ chạy.
6. **Debug kiểm thử trong app** (đoán của controller, người dùng chưa mô tả chi tiết): xem/sửa URL server; kiểm tra server sống + đăng nhập +
   FCM token đã đăng ký; nhật ký sự kiện gần nhất (FCM nhận, gọi API, trạng thái cuộc gọi, WS mở/đóng, lỗi) có nút sao chép; "gọi thử"
   tiếng vọng (echo) để kiểm micro/loa/mạng mà không cần người thứ hai; đo trễ khứ hồi. Màn hình debug chỉ hiện ở bản debug hoặc sau khi
   bật chế độ nhà phát triển (như `devmode` hiện có).

## Không làm

- Video call, chia sẻ màn hình, gọi nhóm, gửi ảnh/file/tin thoại.
- Gọi qua SIM cho cuộc gọi thường. Bản iOS. Đưa lên Play Store / ký bản release (chỉ APK debug, hoặc release ký bằng key debug nếu builder thấy cần).
- Sửa VH/kính, trừ khi buộc phải đổi để khớp (khi đó nêu rõ).

## Điều sẽ khiến việc này hỏng dù "chạy được"

- Cuộc gọi đến không đổ chuông khi máy khoá / app bị tắt → coi như hỏng.
- Lộ khoá Firebase hay token (log, commit, APK chứa khoá server).
- Người không phải liên hệ `active` gọi/nhắn/mở luồng âm thanh được (đúng Review Focus 2 của plan 2).
- Phá phần chủ kính đang chạy (nhận lệnh từ kính, SIM khẩn cấp).

## Ràng buộc kỹ thuật

- Âm thanh: PCM16LE 16 kHz mono, khung 20 ms (640 B), qua WebSocket như `WS /v2/call` (mục 7.3). Mục tiêu trễ một chiều ≤ 400 ms qua Cloudflare.
  Dùng `VOICE_COMMUNICATION` + AEC/NS của Android cho đỡ vọng.
- Quyền mới: `RECORD_AUDIO` (bắt buộc cho gọi). Quyền hiện có giữ nguyên (SIM/SMS cho khẩn cấp dự phòng của chủ kính).
- Build trên máy này: JDK 21 (`~/jdk-21.0.12.1+1`), Android SDK `~/android-sdk` (platform `android-37.0`, build-tools 37.0.0). Không có
  KVM → không chạy emulator; kiểm bằng unit test JVM (+ Robolectric nếu đã có trong dự án), và bằng các script giả lập phía server
  (`relative_sim.py`, `phone_sim.py`). Thử trên máy thật là việc của người dùng.
- AB: Python/FastAPI + Postgres + alembic; test như hiện có (414 pass). Thay đổi API phải xuất lại `contracts/` và cập nhật
  `MOBILE_APP_PROTOCOL.md`.

## Phần "how" controller đã chọn (không phải người dùng chốt — sửa được)

- **Gọi điện thoại ↔ điện thoại đi qua AB:** AB thêm cuộc gọi giữa hai user (không kính) và một cầu WS âm thanh cùng khung/giao thức với
  `WS /v2/call`, để app dùng **một** bộ code gọi điện cho cả hai loại. Cuộc gọi tới liên hệ có kính đi đường kính hiện có.
- **Nhắn tin điện thoại ↔ điện thoại** dùng chung bảng/API messages của AB (mở rộng cho người nhận không có kính), FCM `message_in`.
- **Quan hệ danh bạ dùng `care_links` hiện có**, mở rộng để hai chiều/không cần kính nếu cần; builder quyết cách tối thiểu.
- Nhận diện SeeVia: navy `#041466` chủ đạo, Montserrat (chữ) + Pinyon Script (chỉ cho wordmark), logo/icon từ `SeeVia_system/ui/logo/`.
  Font Be Vietnam Pro hiện tại đổi theo nhận diện nếu ui-ux-pro-max không phản đối vì lý do hiển thị tiếng Việt.

## Để builder quyết

Cấu trúc màn hình/điều hướng cụ thể; tên route; cách ẩn màn hình (cờ tính năng); thư viện WebSocket/audio; schema DB và tên endpoint mới;
xử lý mạng chập chờn (jitter buffer theo `CALL_JITTER_MS=60` như VH); test nào bằng JVM, test nào chỉ thử tay.

## Bàn giao cho người dùng

APK debug tại một đường dẫn rõ ràng (+ cách tải về máy), hướng dẫn cài/cấp quyền/đăng nhập, cách bật debug, danh sách việc thử tay trên
máy thật (đổ chuông khi khoá máy, chất lượng tiếng, pin).

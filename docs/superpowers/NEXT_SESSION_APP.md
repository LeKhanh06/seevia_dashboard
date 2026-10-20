# Session app SeeVia: đọc file này trước

## Đợt 1 (giao tiếp tối thiểu) — xong 2026-10-20

Đã hoàn tất trên nhánh `app-giao-tiep`, chưa push. Tài liệu của đợt: `docs/superpowers/specs/2026-10-20-app-giao-tiep-toi-thieu-design.md` và `docs/superpowers/plans/2026-10-20-app-giao-tiep-toi-thieu.md`. Phần còn lại của spec lớn `docs/superpowers/specs/2026-10-20-seevia-mobile-app-design.md` dành cho đợt sau: gọi/nhắn điện thoại↔điện thoại; danh bạ hai chiều không cần kính và chọn người từ danh bạ máy; sửa alias/ưu tiên bằng `PATCH`; đổi tên/nhận diện SeeVia; ẩn màn hình khi không có server; màn debug echo/RTT.

Cập nhật 2026-10-20. Việc: code app Android SeeVia (đổi từ "Your Eyes") chạy thật, gọi/nhắn qua internet, danh bạ, debug; giao APK.
Spec: `docs/superpowers/specs/2026-10-20-seevia-mobile-app-design.md` (CHƯA được người dùng duyệt chính thức, CHƯA qua builder check).

## Bước tiếp

1. Chạy builder check cho spec (skill brainstorming, `builder-check-prompt.md`), gom câu hỏi "của người dùng" vào MỘT lượt hỏi,
   rồi xin người dùng duyệt spec. Điểm còn là đoán: chi tiết màn hình debug (mục tiêu 6), cách mở rộng `care_links` thành danh bạ hai chiều.
2. Duyệt xong → `superpowers:writing-plans` → thực thi (subagent sonnet, gộp task, review bảo mật + 1 review cuối; xem memory
   `seevia-toi-uu-token-dieu-phoi`). UI qua skill `ui-ux-pro-max`.
3. Làm trên nhánh mới từ `voice-v2-hardening` của AB SAU KHI lượt sửa review cuối (final-fix) đã commit — lượt đó đổi API calls/messages
   (suy user từ kính). Xem `VH/.superpowers/sdd/2026-10-19-glasses-calls-messages/final-fix-report.md`.

## Đã chuẩn bị

- JDK 21: `~/jdk-21.0.12.1+1`. Android SDK: `~/android-sdk` (platform-tools, `platforms;android-37.0`, `build-tools;37.0.0`).
  Build: `JAVA_HOME=~/jdk-21.0.12.1+1 ANDROID_HOME=~/android-sdk ./gradlew test assembleDebug` trong `mobile/` (chưa chạy thử lần nào).
  Gradle wrapper 9.5.0, AGP 9.3.0, compileSdk 37 — nếu `compileSdk = 37` đòi đúng gói `android-37` thì xem lại tên gói.
- Không KVM → không emulator. Test bằng JVM + script giả lập server (`server/scripts/relative_sim.py`, `phone_sim.py`).
- Firebase project `your-537d3`, package `com.youreyes.app` (GIỮ, người dùng chốt).
  - `mobile/app/google-services.json` (git-ignored, quyền 600).
  - Khoá server: `~/.config/seevia/firebase-adminsdk.json` (quyền 600); `SeeVia_system/.env` → `GOOGLE_APPLICATION_CREDENTIALS` trỏ tới đó.
  - KHÔNG commit/copy/in hai file này; người dùng yêu cầu bảo mật. Không `source` `.env`.

## Người dùng đã chốt (đừng hỏi lại)

Xem mục "Người dùng đã chốt" trong spec. Tóm tắt: một app, user bình thường; danh bạ theo số; gọi kính↔điện thoại và điện thoại↔điện thoại,
chỉ thoại internet, không video; khẩn cấp giữ SIM dự phòng; tên hiển thị SeeVia, giữ package; ẩn màn hình không có server.

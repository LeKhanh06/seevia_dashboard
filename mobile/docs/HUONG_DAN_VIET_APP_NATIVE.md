# Hướng dẫn viết tính năng cho app Android (native) SeeVia

Dành cho người viết code trong `mobile/`. App là Android native: Kotlin + Jetpack Compose, package `com.youreyes.app`
(giữ nguyên, tên hiển thị SeeVia). Hiện **chỉ có Android**. Chưa có iOS, nên tài liệu này không hướng dẫn iOS.

Ví dụ xuyên suốt là màn **Thống kê sử dụng** (`stats/`, gọi `GET /api/v1/me/stats`). Đây là màn mẫu nhỏ nhất
đi đủ các lớp: gọi API → đọc JSON → định dạng → ViewModel → màn Compose → điều hướng → test.

## 1. Bản đồ code (`app/src/main/java/com/youreyes/app`)

| Thư mục | Vai trò |
|---|---|
| `command/` | Nhận lệnh từ server qua FCM → `CommandDispatcher` → `CommandStore` (SQLite) → `ReportFlusher` báo kết quả. FCM receiver không tự chạy nghiệp vụ |
| `actions/` | `ActionRegistry` + handler theo nhóm (gọi, nhạc, dẫn đường, xe, trạng thái) |
| `calls/` | Gọi/nhắn qua Internet; **`SessionApi`** dựng client theo phiên đăng nhập và xử lý 401 |
| `network/` | `SessionApiClient` (API cho người dùng đã đăng nhập), `DeviceApiClient` (token thiết bị), `GlassesServerClient`, BLE |
| `care/`, `messages/` | Người thân, chat |
| `stats/` | Thống kê sử dụng của chủ kính (màn mẫu của tài liệu này) |
| `ui/` | Các màn Compose khác, `components/Components.kt` (khối dùng chung), `theme/` (màu, chữ, độ tương phản cao), `navigation/AppNavigation.kt` |
| `core/` | `AppConfig` (SharedPreferences: `base_url`, token phiên…), `FeatureFlags`, `Timestamps` |

Luồng lệnh từ kính và hợp đồng API: `../docs/architecture.md`, `../docs/project-context.md`. Quy tắc bắt buộc: `../claude.md` (mục Android).

## 2. Môi trường build trên máy GPU 192.168.20.156

`~/.gradle` và `~/.venvs` trỏ tới NAS, mà NAS có thể chưa mount. Khi đó dùng bản cục bộ:

```bash
cd mobile
export JAVA_HOME=~/jdk-21.0.12.1+1 ANDROID_HOME=~/android-sdk GRADLE_USER_HOME=~/.gradle-local
sh ./gradlew testDebugUnitTest assembleDebug     # gradlew không có quyền x -> gọi qua sh
```

- APK debug: `app/build/outputs/apk/debug/app-debug.apk`.
- Cần `app/google-services.json` (Firebase, bị gitignore). **Không commit, copy hay in nội dung file này.**
  Ở worktree phụ thì tạo symlink tạm tới bản trong `app-demo-backend/mobile/app/` rồi xoá sau khi build.
- Máy không có KVM nên không chạy được emulator. Test bằng unit test JVM (`app/src/test`), thử giao diện trên máy thật
  (cài APK qua `adb`; xem CLAUDE.md workspace §4 về cổng adb).
- Trên Windows: `.\gradlew.bat test lint assembleDebug` (xem `../README.md`).

## 3. Thêm một màn gọi API — 6 bước (theo `stats/`)

### Bước 1 — Gọi API trong `network/SessionApiClient.kt`

Mỗi endpoint là một hàm trả `ApiResult<T>`. Hàm này không ném ngoại lệ và chạy blocking, nên chỉ gọi ngoài main thread.

```kotlin
fun myStats(from: String, to: String): ApiResult<MyStats> =
    call("GET", "/api/v1/me/stats", query = listOf("from" to from, "to" to to), envelope = false, parse = ::parseMyStats)
```

- Phần lớn API của AB bọc kết quả trong `{"status":"ok","data":{…}}`, và `call` mặc định bóc `data`. `/api/v1/me/stats`
  thì trả object trần, nên phải truyền `envelope = false`. Xem phản hồi thật trong test của AB trước khi chọn.
- `ApiResult`: `Ok`, `Failed(httpStatus, code, message)` (đọc envelope lỗi `{"error":{code,message}}`), `Unauthorized` (401), `Network`.

### Bước 2 — Đọc JSON thành data class (`stats/MyStats.kt`)

Dùng `org.json`, đọc khoan dung: trường thiếu hoặc `null` thành `0`/`null`, không làm hỏng cả màn. Tỷ lệ của AB là **phần trăm 0–100**
(một chữ số thập phân), hoặc `null` khi không có mẫu.

### Bước 3 — Định dạng thuần, không dùng kiểu Android (`stats/StatsFormat.kt`)

Mọi chữ hiển thị và **câu đọc cho TalkBack** sinh ở đây. Phần này test được bằng JUnit thường, không cần thiết bị.

- minSdk 24 và không bật desugaring, nên **không dùng `java.time`**. Dùng `Calendar`/`SimpleDateFormat` với múi giờ
  `Asia/Ho_Chi_Minh` (ngày thống kê là ngày Việt Nam).
- Số: `87,5%`, `2 giờ 1 phút`. Câu đọc: "87,5 phần trăm", "chưa có số liệu" (không đọc "gạch ngang").

### Bước 4 — ViewModel (`stats/StatsViewModel.kt`)

Theo mẫu `care/CareViewModel.kt`: `AndroidViewModel`, `MutableStateFlow<UiState>`, mạng chạy trên `Dispatchers.IO`
qua `SessionApi.run(context) { it.myStats(...) }`.

- `SessionApi.run` tự trả `Unauthorized` khi chưa đăng nhập, và tự xoá token khi gặp 401.
- Câu lỗi tiếng Việt lấy từ `calls/ApiErrorText.kt::apiErrorVi`. Mã lỗi riêng của màn thì bọc thêm (vd. `statsErrorVi` cho `USAGE_NOT_CONFIGURED`).
- Đổi tham số trong lúc đang tải (vd. đổi kỳ) thì bỏ kết quả cũ.

### Bước 5 — Màn Compose (`stats/StatsScreen.kt`)

Tách `XxxRoute` (lấy ViewModel, gọi `refresh()` trong `LaunchedEffect`) khỏi `XxxScreen` (chỉ nhận state + callback).
Dùng khối có sẵn trong `ui/components/Components.kt`, không tự vẽ lại:

| Khối | Dùng cho |
|---|---|
| `ScreenShell(title, onBack)` | Khung màn, cuộn, nút quay lại nhìn thấy được |
| `SectionLabel` | Tiêu đề mục, đã đánh dấu heading cho TalkBack (người dùng nhảy giữa các mục) |
| `PanelCard`, `RowCard` | Thẻ nội dung; dòng mở màn khác |
| `MiniStat` | Một con số kèm nhãn |
| `ChoiceChip` | Chọn một trong vài lựa chọn (tối thiểu `MinTouchTarget` = 56 dp) |
| `GradientButton` (một nút chính mỗi màn), `SecondaryButton` | Nút |
| `InlineMessage`, `EmptyState` | Lỗi / trạng thái rỗng |

Màu lấy từ `AppTheme.colors` (đã có bộ **tương phản cao**). Không viết mã màu hex trong màn.

### Bước 6 — Điều hướng và lối vào

1. `ui/navigation/AppNavigation.kt`: thêm hằng vào `object Routes`, rồi thêm `composable(Routes.X) { XRoute(..., onBack = back) }`
   trong `appDestinations` (màn đẩy lên thì luôn có `onBack`).
2. Thêm lối vào là một `RowCard` ở màn cha (Thống kê nằm ở **Hồ sơ**: `ui/profile/ProfileScreen.kt`), truyền callback
   `onNavigateToX` qua cả `ProfileRoute` lẫn `ProfileScreen`.

## 4. Trợ năng — bắt buộc (người dùng khiếm thị dùng TalkBack)

- **Thứ tự đọc:** câu tóm tắt trước, rồi các con số, rồi chi tiết. Mỗi khối thông tin là **một** điểm dừng:
  `Modifier.clearAndSetSemantics { contentDescription = "<câu đầy đủ>" }`.
- **Biểu đồ chỉ là phần phụ.** Cả biểu đồ là một điểm dừng, đọc câu tóm tắt (ngày nhiều nhất, trung bình), kèm nút
  "Đọc số từng ngày" để mở danh sách chữ. Không bắt người dùng vuốt qua 30 cột.
- **Màu không bao giờ là tín hiệu duy nhất.** Trạng thái luôn có chữ đi kèm.
- **Chữ phóng to:** đọc `LocalTypeScale.current`; khi > 1,25 thì chuyển lưới 2 cột thành 1 cột để chữ không bị cắt.
- Vùng chạm ≥ `MinTouchTarget`. Icon chỉ để trang trí thì để `contentDescription = null` (hoặc gộp vào nút có chữ).
- Nội dung đổi sau khi tải: đặt `liveRegion = LiveRegionMode.Polite` cho câu tóm tắt.
- Không log số điện thoại, tên, nội dung, token ở dạng thô (`../claude.md` mục Android).

## 5. Kiểm thử

- Logic thuần (đọc JSON, định dạng, câu đọc): JUnit trong `app/src/test/java/...`. Mẫu: `stats/StatsFormatTest.kt`
  dùng đúng JSON mà AB trả (lấy từ test tích hợp AB `server/tests/integration/test_admin_stats_postgres.py`).
- Có test kiểm mọi khoá chức năng của server đều có nhãn tiếng Việt. Khi AB thêm khoá mới vào `GROUPS`
  (`server/src/app/services/admin_stats.py`) thì test này nhắc thêm nhãn.
- Trước khi giao: `sh ./gradlew testDebugUnitTest assembleDebug` phải qua (2026-11-18 trên máy GPU: 297 test pass, APK debug 25 MB). Sau đó thử trên máy thật với TalkBack bật.

## 6. Hợp đồng `GET /api/v1/me/stats` (AB nhánh `thong-ke-su-dung`)

Đăng nhập bằng phiên app (`Authorization: Bearer <access token>`), tham số `from`, `to` (`yyyy-MM-dd`, gồm cả hai đầu,
không quá 400 ngày). Phản hồi trần:

```json
{"range":{"from":"…","to":"…"},
 "totals":{"uses":0,"completion_rate":null,"first_try_rate":null,"avg_retries":null,"usage_seconds":0,
           "connection_seconds":0,"alert_mode_seconds":0,"active_days":0,"internet_calls":0,
           "internet_calls_answered":0,"sos":0},
 "features":[{"key":"ocr","uses":9,"completion_rate":77.8}],
 "by_day":[{"date":"2026-11-18","uses":3}]}
```

Lỗi: 401 (chưa đăng nhập), 400 `INVALID_REQUEST` (kỳ sai), 503 `USAGE_NOT_CONFIGURED` (server chưa đặt `USAGE_ID_KEY`).
Tài khoản chưa có dữ liệu trả 200 với số 0/null, không trả 404.

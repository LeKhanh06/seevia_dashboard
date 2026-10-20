# App SeeVia — giao tiếp tối thiểu với kính: kế hoạch thực thi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Người thân gọi/nghe/nhắn với người đeo kính qua app Android (hợp đồng mục 7), và đường lệnh kính → điện thoại không còn trễ/hết hạn/mất report.

**Architecture:** FCM receiver tách hai đường: có `type` → `calls/` (định tuyến, máy trạng thái, Telecom self-managed, âm thanh WS); có `request_id` → đường lệnh cũ, nay hai làn + kiểm hạn + retry bằng WorkManager. Phần logic thuần (router, máy trạng thái, jitter buffer, chọn làn, hết hạn, phân loại lỗi report) tách khỏi Android để test JVM.

**Tech Stack:** Kotlin 2.3, Compose (BOM 2026.06), AGP 9.3, minSdk 24 / compileSdk 37, OkHttp (WebSocket + REST mới), WorkManager, Telecom `ConnectionService`, JUnit4 + `org.json`.

**Spec:** `docs/superpowers/specs/2026-10-20-app-giao-tiep-toi-thieu-design.md` (đã duyệt). Hợp đồng: `docs/MOBILE_APP_PROTOCOL.md` mục 3–5, 4.1, 7.

## Global Constraints

- Làm trên nhánh mới `app-giao-tiep` của AB tạo từ `voice-v2-hardening` HEAD; VH: nhánh `app-giao-tiep` từ `voice-v2-hardening`. Không push/merge main.
- Build: trong `mobile/`: `JAVA_HOME=~/jdk-21.0.12.1+1 ANDROID_HOME=~/android-sdk ./gradlew test assembleDebug`.
- Package giữ `com.youreyes.app`. Không đổi tên app/nhận diện đợt này.
- **Không** đọc/in/copy/commit `mobile/app/google-services.json`, `~/.config/seevia/firebase-adminsdk.json`, `SeeVia_system/.env` (không `source`).
- Không log raw: `media_token`, access token, Device token, số điện thoại đầy đủ, nội dung tin nhắn. Số điện thoại hiển thị/log dạng `***768`.
- API mục 7 dùng `Authorization: Bearer <AppConfig.KEY_ACCESS_TOKEN>`; Device token chỉ cho `/device/*`.
- Âm thanh: PCM16LE, 16 000 Hz, mono, khung 20 ms = **640 byte**; jitter phía nhận **60 ms**; gửi đều mỗi 20 ms, không gộp.
- Kính = âm thanh, điện thoại = chữ: mọi `error.code` mới phải có câu nói ở `VH/handlers/result_speech.py`; không thêm câu nói bảo người khiếm thị "xem thông báo".
- Chữ trên app tiếng Việt; mọi nút cuộc gọi có `contentDescription`, vùng chạm ≥ 64 dp.
- Hai phụ thuộc mới đã được duyệt: OkHttp `com.squareup.okhttp3:okhttp:4.12.0`, WorkManager `androidx.work:work-runtime-ktx:2.10.1` (nếu không tải được bản này thì bản ổn định gần nhất, ghi lại). Không thêm phụ thuộc nào khác mà không hỏi.
- Không đổi hợp đồng public. AB server test (420 pass) và VH test phải giữ xanh.
- Xong mỗi task: thêm một dòng vào "Nhật ký đã làm" của `SeeVia_system/docs/2026-10-20-ra-soat-giao-tiep-vatcan-ocr-benchmark.md` (SeeVia_system không phải repo git — chỉ sửa file).

## Review Focus

1. **FCM tới trễ/trùng/đảo thứ tự** (`call_ended` tới trước `call_incoming`, `call_incoming` lặp): không đổ chuông cho cuộc đã kết thúc, không đổ hai lần — test ở Task 4 (`CallStateMachineTest`).
2. **Bấm Nghe khi cuộc đã hết chuông/`answer` trả 409 hoặc 401**: dừng chuông, hiện lý do, không mở WS — test ở Task 4.
3. **Mạng chập chờn trong cuộc gọi** (khung tới dồn cục, thiếu khung, khung sai cỡ): phát im lặng thay vì giật/crash, bỏ khung ≠ 640 B — test ở Task 5 (`JitterBufferTest`).
4. **Lệnh gấp xếp sau lệnh chậm** (`emergency_call` khi `music_play` đang chờ 45 s): chạy ngay ở làn nhanh — test ở Task 2 (`CommandLanesTest`).
5. **Cuộc gọi VoIP của app bị coi như cuộc gọi SIM** (IncomingCallMonitor báo `call_incoming` lên kính): monitor bỏ qua khi `ActiveVoipCall.isActive` — test ở Task 6.

---

### Task 1: Build nền + phụ thuộc mới

**Files:**
- Modify: `mobile/app/build.gradle.kts` (dependencies)
- Create: `mobile/BUILD_NOTES.md` chỉ khi phải sửa gì để build chạy (ghi lại vì sao)

**Interfaces:** Produces: dự án build + test xanh; OkHttp và WorkManager dùng được.

- [ ] **Step 1:** Tạo nhánh AB `app-giao-tiep` từ `voice-v2-hardening`. Commit spec + plan đang untracked vào nhánh này.
- [ ] **Step 2:** Chạy build như Global Constraints **trước khi đổi gì**. Expected: `BUILD SUCCESSFUL`. Lỗi do môi trường (tên gói SDK `android-37` vs `android-37.0`, thiếu build-tools, wrapper không tải được) → sửa cấu hình tối thiểu, ghi `BUILD_NOTES.md`. Lỗi code có sẵn → dừng, báo controller (không tự sửa logic ngoài phạm vi).
- [ ] **Step 3:** Thêm hai phụ thuộc (Global Constraints). Chạy lại build. Expected: `BUILD SUCCESSFUL`, số test pass bằng Step 2.
- [ ] **Step 4:** Commit `build: thêm OkHttp + WorkManager cho gọi/nhắn và retry report`.

### Task 2: Đường lệnh — hạn, hai làn, report retry, onTimeout (+ câu nói VH)

**Files:**
- Modify: `mobile/app/src/main/java/com/youreyes/app/command/CommandDispatcher.kt` (`PushCommand`, `ReportSender`, `sendOnce`)
- Modify: `.../command/ReportFlusher.kt`, `.../command/CommandExecutionService.kt`, `.../command/FcmPushReceiver.kt`, `.../network/DeviceApiClient.kt` (`asReportSender`, `request` lộ mã HTTP)
- Create: `.../command/CommandLanes.kt`, `.../command/ReportRetryWorker.kt`
- Test: `mobile/app/src/test/java/com/youreyes/app/command/CommandPipelineTest.kt` (sửa FakeSender), `CommandLanesTest.kt`
- VH: `visioncare-host-demo/handlers/result_speech.py` (`_ERROR_SPEECH`), test VH tương ứng hiện có cho `_ERROR_SPEECH`

**Interfaces:**
- Produces:
  - `PushCommand` thêm `expiresAtMillis: Long?` (parse `expires_at` ISO-8601; lỗi parse → `null` = không kiểm hạn) và `mapsUrl: String?`. `CommandExecutionService.start/readCommand` chuyển đủ 7 khoá.
  - `enum class ReportOutcome { SENT, RETRY, REJECTED }`; `fun interface ReportSender { fun send(credentials: DeviceCredentials, payload: DeviceReportPayload): ReportOutcome }`. `DeviceApiClient.asReportSender()`: 2xx → SENT; 400/404/409/422 → REJECTED; còn lại (mạng, 5xx, 401, 429) → RETRY.
  - `sendOnce` trả `ReportOutcome`; REJECTED → `ReportStatus.ABANDONED` ngay.
  - `object CommandLanes { fun laneFor(action: String): Lane }`, `enum class Lane { FAST, SLOW }`. FAST = `emergency_call, call_answer, call_reject, location_get, capabilities_get`; còn lại SLOW (kể cả action lạ).
  - `ReportRetryWorker` (CoroutineWorker): chạy `ReportFlusher.flush`; còn report chưa gửi → `Result.retry()`. Lịch: `OneTimeWorkRequest` unique `"report-retry"` `ExistingWorkPolicy.KEEP`, constraint `NetworkType.CONNECTED`, backoff exponential 15 s. Được enqueue khi một report gửi thất bại (RETRY).

- [ ] **Step 1: Test hạn** trong `CommandPipelineTest`:
  `expired_command_is_not_executed_and_reports_COMMAND_EXPIRED` — clock = expires + 1 ms; handler giả đếm số lần gọi → 0; report `failed`, `error.code == "COMMAND_EXPIRED"`, ghi `request_id` (lần hai cùng id → `DuplicateIgnored`).
  `command_without_expires_at_still_runs` — `expiresAtMillis = null` → handler chạy 1 lần.
  `fromData_parses_expires_at_and_maps_url` — `"2026-10-19T08:01:00.000000+00:00"` → `1792396860000`; `maps_url` giữ nguyên.
- [ ] **Step 2: Test report** `rejected_report_is_abandoned_immediately` (sender trả REJECTED → status `ABANDONED`, attempts 1, flush lần sau không gửi lại); `retry_report_stays_failed` (RETRY → `FAILED`). Sửa FakeSender sang `ReportOutcome`.
- [ ] **Step 3: Test làn** `CommandLanesTest`: 5 action FAST ở trên → `FAST`; `music_play`, `contact_call`, `navigation_start`, `"unknown_x"` → `SLOW`.
- [ ] **Step 4:** Chạy `./gradlew test` → các test mới FAIL (chưa có symbol).
- [ ] **Step 5:** Cài đặt: dispatcher kiểm hạn **sau** `insertCommandIfNew` và trước handler (message `"Command expired before execution"`); `ReportOutcome`; `CommandLanes`; service giữ hai `Executors.newSingleThreadExecutor()` (FAST/SLOW) ở `companion object` (sống qua các lần start), đếm lệnh đang chạy, `stopSelf()` khi cả hai làn rỗng (thay `stopSelf(startId)`); override `onTimeout(startId, fgsType)` (API 34+): ghi log, đánh dấu cờ để lệnh đang chạy, khi trả về, vẫn lưu report bình thường; gọi `stopSelf()`. Enqueue `ReportRetryWorker` khi RETRY.
- [ ] **Step 6:** VH: thêm `"COMMAND_EXPIRED": "Điện thoại nhận lệnh quá muộn nên chưa làm. Bạn nói lại giúp tôi nhé."` vào `_ERROR_SPEECH`; test VH: `speak_result("music_play", {"request_state":"failed","error":{"code":"COMMAND_EXPIRED"}})` trả đúng câu. Chạy `~/.venvs/seevia/bin/python -m pytest tests -q` trong VH → xanh (515 pass + 2 skip trở lên).
- [ ] **Step 7:** `./gradlew test assembleDebug` xanh. Commit AB `command: kiểm expires_at, hai làn lệnh, report retry bằng WorkManager, onTimeout`; commit VH `result_speech: câu nói cho COMMAND_EXPIRED`.

### Task 3: `navigation_start` theo hợp đồng 4.1

**Files:** Modify `.../actions/NavigationActions.kt`, `.../command/CommandDispatcher.kt` (truyền `mapsUrl` vào handler qua params: dispatcher chèn `"maps_url"` vào `paramsJson` nếu có và params chưa có khoá đó). Test: `.../actions/NavigationUrlTest.kt`.

**Interfaces:** Produces `object NavigationUrls { fun target(dest: JSONObject): String?; fun webUrl(target: String): String; fun appUri(target: String): String }`.

- [ ] **Step 1: Test** `NavigationUrlTest`: `target({"address":"Bưu điện Thành phố Hồ Chí Minh"})` = address; `target({"lat":10.7799,"lng":106.6999})` = `"10.7799,106.6999"`; `target({"lat":10.7})` = null; `webUrl("Bưu điện Thành phố Hồ Chí Minh")` = đúng chuỗi ví dụ `maps_url` trong hợp đồng mục 3.1; `appUri(t)` = `"google.navigation:q=" + Uri-encode(t) + "&mode=w"` (dùng `java.net.URLEncoder` + thay `+`→`%20` để test JVM được).
- [ ] **Step 2:** Chạy test → FAIL.
- [ ] **Step 3:** Cài đặt theo mục 4.1 bước 2–5: thông báo heads-up kênh `appdemo_commands`, tiêu đề `"Chỉ đường tới {address}"`, nội dung `"chạm để mở Google Maps"`; `PendingIntent` mở Maps (có package `com.google.android.apps.maps` thì `appUri`, không thì `ACTION_VIEW` `maps_url`/`webUrl`); **không** tự mở activity; `succeeded` ngay khi `NotificationManagerCompat.areNotificationsEnabled()` và `notify` không ném; ngược lại `NAVIGATION_PROVIDER_ERROR`; thiếu đích `INVALID_DESTINATION`. Kết quả giữ schema cũ.
- [ ] **Step 4:** Test + build xanh. Commit `navigation_start: thông báo chạm để mở Maps (hợp đồng 4.1)`.

### Task 4: Client API phiên + router FCM + máy trạng thái cuộc gọi (logic thuần)

**Files:**
- Create: `.../network/SessionApiClient.kt`, `.../calls/CallModels.kt`, `.../calls/FcmCallRouter.kt`, `.../calls/CallStateMachine.kt`
- Modify: `.../command/FcmPushReceiver.kt` (rẽ nhánh theo `type`)
- Test: `.../calls/FcmCallRouterTest.kt`, `.../calls/CallStateMachineTest.kt`, `.../network/SessionApiClientTest.kt` (parse JSON từ chuỗi mẫu hợp đồng, không gọi mạng)

**Interfaces:**
- Produces:
  - `sealed interface ApiResult<out T> { data class Ok<T>(val data: T); data class Failed(val httpStatus: Int, val code: String, val message: String); data object Unauthorized; data class Network(val cause: Throwable) }`.
  - `class SessionApiClient(baseUrl: String, accessToken: String, client: OkHttpClient)` với: `careLinks(): ApiResult<List<CareLink>>`, `invite(phone: String, displayName: String, aliases: List<String>, emergency: Boolean, priority: Int): ApiResult<CareLink>`, `accept(linkId)`, `revoke(linkId)`, `startCall(ownerUserId: String): ApiResult<String>` (call_id), `answer(callId): ApiResult<CallAnswer>`, `reject(callId): ApiResult<CallInfo>`, `hangup(callId): ApiResult<CallInfo>`, `getCall(callId): ApiResult<CallInfo>`, `sendMessage(ownerUserId, text, requestId: String): ApiResult<String>`, `messages(withUserId: String, before: String? = null, limit: Int = 50): ApiResult<List<ChatMessage>>`. 401 → `Unauthorized` (người gọi xoá `KEY_ACCESS_TOKEN`, xem Task 6/7). Parse tách thành hàm `internal fun parseCareLink(JSONObject)`, `parseCallInfo`, `parseCallAnswer`, `parseMessage`, `parseError(status, body)` để test.
  - Model (`CallModels.kt`): `CareLink(id, role, status, displayName, aliases, emergency, priority, ownerUserId, relativeUserId, peerDisplayName, peerPhoneMasked)` — **chỉ lưu số đã che** (`***` + 3 số cuối); `CallInfo(callId, direction, state, endReason, emergency, peerDisplayName, mediaUrl, mediaToken)`; `CallAnswer(callId, mediaUrl, mediaToken: String?)`; `ChatMessage(id, direction, fromMe, text, createdAt, readAt)`.
  - `sealed interface CallPush { IncomingCall(callId, fromName, emergency: Boolean); Answered(callId, mediaUrl, mediaToken); Ended(callId, reason); MessageIn(messageId, fromName, text) }`; `object FcmCallRouter { fun parse(data: Map<String,String>): CallPush? }` — `type` lạ/thiếu khoá → null. Receiver: `data["type"] != null` → `FcmCallRouter` → `CallController.onPush` (Task 6); ngược lại đường lệnh cũ.
  - `CallStateMachine` thuần (không Android): trạng thái `Idle | Ringing(callId, name, emergency) | Outgoing(callId) | Connecting(callId) | Active(callId, sinceMillis) | Ended(callId, reasonVi: String)`; sự kiện `Push(CallPush)`, `UserAnswer`, `UserReject`, `UserHangup`, `UserCall(ownerUserId)`, `ApiReply(...)`, `SocketState(state, reason)`, `SyncResult(CallInfo)`. Hàm `fun reduce(state, event): Pair<CallState, List<Effect>>`; `Effect`: `StartRinging, StopRinging, CallApi(op, callId), OpenMedia(url, token), CloseMedia, ShowEnded(reasonVi)`. Nhớ `endedIds` (≤ 50 id gần nhất) để bỏ push trễ.
  - `fun endReasonVi(reason: String?): String` — `busy`→"Kính đang bận", `glasses_offline`→"Kính không kết nối", `missed|timeout`→"Không ai nghe máy", `rejected`→"Cuộc gọi bị từ chối", `revoked`→"Đã huỷ liên kết người thân", `media_timeout|peer_disconnect`→"Mất kết nối âm thanh", `max_duration`→"Cuộc gọi quá 2 giờ", còn lại→"Cuộc gọi đã kết thúc".

- [ ] **Step 1: Test router** — 4 payload mẫu mục 7.6 → đúng `CallPush`; `emergency:"true"` → true; thiếu `call_id` → null; payload lệnh cũ (có `request_id`, không `type`) → null.
- [ ] **Step 2: Test máy trạng thái** (`CallStateMachineTest`), mỗi ca một test:
  `incoming_rings_once_for_duplicate_push`; `ended_before_incoming_never_rings` (Ended rồi IncomingCall cùng id → vẫn Idle, không `StartRinging`); `answer_409_stops_ringing_and_shows_ended` (`ApiReply(Failed(409,"CALL_STATE_CONFLICT"))` → `Ended("Cuộc gọi đã kết thúc")`, có `StopRinging`, không `OpenMedia`); `answer_unauthorized_shows_relogin` (→ `Ended("Phiên đăng nhập hết hạn")`); `answer_ok_opens_media_with_token`; `answer_ok_without_token_syncs` (`mediaToken=null` → `CallApi(GET)`); `outgoing_answered_push_opens_media`; `outgoing_busy_ends_with_vi_reason` ("Kính đang bận"); `hangup_sends_api_and_closes_media`; `socket_ended_moves_to_ended`; `second_incoming_while_active_is_rejected` (cuộc mới khi đang Active → `CallApi(REJECT, newId)`, giữ cuộc cũ).
- [ ] **Step 3: Test parse API** — `parseCareLink` từ JSON mẫu 7.1 → `peerPhoneMasked == "***001"`; `parseCallAnswer` mẫu 7.2; `parseError(404, envelope)` → code `CARE_LINK_NOT_FOUND`.
- [ ] **Step 4:** Chạy → FAIL. **Step 5:** Cài đặt. **Step 6:** `./gradlew test assembleDebug` xanh. Commit `calls: client API phiên, router FCM, máy trạng thái cuộc gọi`.

### Task 5: Âm thanh cuộc gọi — jitter buffer, WebSocket, engine

**Files:** Create `.../calls/audio/JitterBuffer.kt`, `.../calls/audio/CallMediaSocket.kt`, `.../calls/audio/CallAudioEngine.kt`. Test `.../calls/audio/JitterBufferTest.kt`, `.../calls/audio/MediaProtocolTest.kt`.

**Interfaces:**
- Produces:
  - `class JitterBuffer(frameBytes: Int = 640, targetMs: Int = 60, frameMs: Int = 20, maxMs: Int = 200)`: `fun push(frame: ByteArray)` (bỏ khung ≠ `frameBytes`; quá `maxMs` thì bỏ khung cũ nhất); `fun pop(): ByteArray` — chưa đủ `targetMs` lúc khởi đầu hoặc rỗng → trả 640 byte 0 (im lặng) và tính `underruns`; `val underruns: Int`, `val dropped: Int`.
  - `object MediaProtocol { fun hangup(): String = """{"t":"hangup"}"""; fun mute(on: Boolean): String; fun parseState(text: String): Pair<String, String?>? }`.
  - `class CallMediaSocket(client: OkHttpClient, url: String, token: String, listener: Listener)`; `interface Listener { fun onAudio(frame: ByteArray); fun onState(state: String, reason: String?); fun onClosed(code: Int) }`; `fun sendAudio(frame: ByteArray)`, `fun hangup()`, `fun close()`. URL = `mediaUrl + "?token=" + token`; **không log URL** (chứa token). Mã đóng 4401 → `onClosed(4401)`.
  - `class CallAudioEngine(socket: CallMediaSocket)`: `start()`, `stop()`, `setMuted(Boolean)` (ngừng gửi khung + gửi `mute`), `setSpeaker(Boolean)`. Thu `AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, 16000, MONO, PCM_16BIT)`, bật `AcousticEchoCanceler`/`NoiseSuppressor` nếu `isAvailable()`; phát `AudioTrack` `USAGE_VOICE_COMMUNICATION`, luồng phát lấy `jitter.pop()` mỗi 20 ms. `AudioManager.mode = MODE_IN_COMMUNICATION` khi start, trả lại khi stop; xin `AUDIOFOCUS_GAIN_TRANSIENT`.

- [ ] **Step 1: Test** `JitterBufferTest`: `prebuffers_60ms_before_playing` (2 push → pop trả im lặng; push thứ 3 → pop trả khung 1); `underrun_returns_silence_and_counts`; `wrong_size_frame_dropped`; `burst_over_200ms_drops_oldest` (push 15 khung → giữ ≤ 10, `dropped` = 5); `order_preserved`. `MediaProtocolTest`: `parseState("""{"t":"state","state":"ended","reason":"glasses_hangup"}""")` = `("ended","glasses_hangup")`; text rác → null.
- [ ] **Step 2:** FAIL. **Step 3:** Cài đặt. **Step 4:** test + build xanh. Commit `calls: jitter buffer, WebSocket âm thanh, engine thu/phát`.

### Task 6: Telecom self-managed, màn hình cuộc gọi, controller, guard SIM

**Files:**
- Create: `.../calls/CallController.kt` (singleton nối `CallStateMachine` ↔ effect thật), `.../calls/telecom/SeeViaConnectionService.kt`, `.../calls/telecom/SeeViaConnection.kt`, `.../calls/CallForegroundService.kt`, `.../calls/CallNotifications.kt`, `.../calls/ui/CallActivity.kt`, `.../calls/ui/CallScreen.kt`, `.../calls/ActiveVoipCall.kt`
- Modify: `mobile/app/src/main/AndroidManifest.xml`, `.../telephony/IncomingCallMonitor.kt` (+ receiver PHONE_STATE)
- Test: `.../telephony/IncomingCallMonitorGuardTest.kt` (logic guard tách thành hàm thuần)

**Interfaces:**
- Consumes: Task 4 (`CallStateMachine`, `SessionApiClient`, `CallPush`), Task 5 (`CallAudioEngine`, `CallMediaSocket`).
- Produces: `object CallController { fun onPush(context, push: CallPush); fun answer(); fun reject(); fun hangup(); fun call(context, ownerUserId: String, name: String); fun mute(Boolean); fun speaker(Boolean); val state: StateFlow<CallState> }`; `object ActiveVoipCall { @Volatile var isActive: Boolean }` (true từ Ringing/Outgoing tới Ended); `fun shouldReportSimCall(voipActive: Boolean): Boolean` dùng trong `IncomingCallMonitor`.

- [ ] **Step 1: Test guard** `IncomingCallMonitorGuardTest`: `shouldReportSimCall(true) == false`, `(false) == true`; monitor gọi hàm này trước `sendEvent("call_incoming"/"call_ended")`.
- [ ] **Step 2:** FAIL → cài guard → PASS.
- [ ] **Step 3:** Manifest: quyền `MANAGE_OWN_CALLS`, `RECORD_AUDIO`, `FOREGROUND_SERVICE_PHONE_CALL`, `FOREGROUND_SERVICE_MICROPHONE`; service `SeeViaConnectionService` (`android:permission="android.permission.BIND_TELECOM_CONNECTION_SERVICE"`, intent-filter `android.telecom.ConnectionService`); `CallForegroundService` `foregroundServiceType="phoneCall|microphone"`; `CallActivity` `showWhenLocked`, `turnScreenOn`, `launchMode="singleTop"`, `excludeFromRecents`.
- [ ] **Step 4:** Cài đặt đổ chuông: đăng ký `PhoneAccount` self-managed (API 26+) lúc app khởi động; `IncomingCall` → `TelecomManager.addNewIncomingCall`; `SeeViaConnection.onShowIncomingCallUi` → thông báo `CATEGORY_CALL` full-screen intent `CallActivity` + nhạc chuông (kênh `seevia_calls` IMPORTANCE_HIGH; khẩn cấp kênh `seevia_emergency_calls` IMPORTANCE_HIGH, `setBypassDnd(true)`); `onAnswer/onReject/onDisconnect` → `CallController`. `onCreateIncomingConnectionFailed` hoặc API < 26 → chỉ thông báo full-screen (fallback). Trước khi đổ chuông `getCall(callId)` (đồng bộ, tối đa 3 s): `state != ringing` → không đổ chuông.
- [ ] **Step 5:** `CallScreen` (Compose, **qua skill `ui-ux-pro-max`**): Đổ chuông: tên, nhãn "KHẨN CẤP" nổi bật nếu emergency, hai nút Nghe/Từ chối; Đang gọi: "Đang gọi {tên}…", Huỷ; Đang nói: đồng hồ mm:ss, Tắt mic, Loa ngoài, Cúp; Kết thúc: lý do `endReasonVi`, tự đóng sau 3 s. Nút ≥ 64 dp, `contentDescription` tiếng Việt.
- [ ] **Step 6:** `Unauthorized` ở bất kỳ API nào → xoá `KEY_ACCESS_TOKEN`, thông báo "Phiên đăng nhập hết hạn — mở app để đăng nhập lại" (mở `MainActivity` → route `AUTH`).
- [ ] **Step 7:** `./gradlew test assembleDebug` xanh; `aapt2 dump xmltree`/`apkanalyzer manifest print` trên APK xác nhận quyền + service có mặt. Commit `calls: Telecom self-managed, màn hình cuộc gọi, guard cuộc gọi SIM`.

### Task 7: Người thân + nhắn tin + thông báo tin

**Files:** Create `.../care/CareScreen.kt`, `.../care/CareViewModel.kt`, `.../care/CareUiState.kt`, `.../messages/ChatScreen.kt`, `.../messages/ChatViewModel.kt`, `.../messages/MessageNotifications.kt`. Modify `.../ui/navigation/AppNavigation.kt` (route `CARE`, `CHAT/{peerUserId}`; lối vào từ Home), `.../calls/CallController.kt` (`MessageIn` → thông báo). Test `.../care/CareUiStateTest.kt`, `.../messages/ChatUiStateTest.kt`.

**Interfaces:** Consumes `SessionApiClient`, `CareLink`, `ChatMessage`, `CallController.call`.
- `CareUiState.from(links: List<CareLink>, myUserId: String)` → `pendingToAccept` (role relative + pending), `myGlassesOwners` (role relative + active: nút Gọi/Nhắn), `myRelatives` (role owner: active/pending, nút Thu hồi), có `isEmpty`.
- `ChatUiState.merge(server: List<ChatMessage>, incoming: List<ChatMessage>)` — gộp theo `id`, sắp cũ → mới để hiển thị; tin `from_me=false` có `text` bắt đầu `"KHẨN CẤP"` → `isEmergency=true`; nhãn trạng thái tin gửi đi: `readAt != null` → "Đã nghe", ngược lại "Đã gửi"; đang gửi → "Đang gửi"; lỗi → "Gửi lỗi — chạm để gửi lại" (gửi lại cùng `request_id`).

- [ ] **Step 1: Test** `CareUiStateTest`: phân nhóm đúng cho 4 link mẫu (relative-pending, relative-active, owner-active, owner-pending); link revoked không xuất hiện. `ChatUiStateTest`: gộp trùng id; thứ tự; nhãn "Đã nghe"/"Đã gửi"; tin KHẨN CẤP; gửi lại giữ `request_id`.
- [ ] **Step 2:** FAIL → cài đặt (UI qua `ui-ux-pro-max`; form mời: số điện thoại, tên hiển thị, alias phân cách dấu phẩy ≤ 10, công tắc khẩn cấp, ưu tiên 0–100; lỗi `USER_NOT_FOUND` → "Số này chưa có tài khoản SeeVia", `CARE_LINK_CONFLICT` → "Đã mời hoặc đã là người thân"). `message_in` → thông báo kênh `seevia_messages` (KHẨN CẤP: kênh `seevia_emergency_messages`, IMPORTANCE_HIGH), chạm mở `CHAT`; nếu đang mở đúng hội thoại thì chèn thẳng. Thông báo **không** đưa nội dung tin vào log.
- [ ] **Step 3:** `./gradlew test assembleDebug` xanh. Commit `care+messages: người thân, hội thoại, thông báo tin`.

### Task 8: Kiểm cuối + bàn giao

**Files:** Create `mobile/docs/THU_TAY_GIAO_TIEP.md`. Modify `docs/MOBILE_APP_PROTOCOL.md` (chỉ mục mô tả hành vi app: hai làn, `COMMAND_EXPIRED`, retry WorkManager — không đổi hợp đồng server). Modify file rà soát (Nhật ký).

- [ ] **Step 1:** `./gradlew test assembleDebug lint` trong `mobile/` → xanh (lint: không thêm lỗi mới so với Task 1). AB server: `uv run pytest -q` với `TEST_DATABASE_URL` (Postgres test cổng 55432) → 420 pass. VH: pytest xanh.
- [ ] **Step 2:** `grep -rn "media_token\|Bearer \|access_token" mobile/app/src/main` — mọi chỗ log không in giá trị. `git status` không có `google-services.json`.
- [ ] **Step 3:** Viết `THU_TAY_GIAO_TIEP.md`: cài APK (`mobile/app/build/outputs/apk/debug/app-debug.apk`), cấp quyền (micro, thông báo, toàn màn hình, tối ưu pin, tài khoản gọi), kịch bản: (1) mời/chấp nhận; (2) kính gọi → đổ chuông khi khoá máy, khi app bị vuốt tắt; (3) gọi kính, kính bận/offline; (4) chất lượng tiếng/vọng, loa ngoài, tắt mic, tai nghe Bluetooth; (5) khẩn cấp; (6) nhắn tin hai chiều, "Đã nghe"; (7) đang có cuộc SIM; (8) lệnh `emergency_call` khi đang phát nhạc; (9) tắt mạng rồi bật → report được gửi.
- [ ] **Step 4:** Cập nhật Nhật ký trong file rà soát + `docs/superpowers/NEXT_SESSION_APP.md` (đợt 1 xong, đợt 2 = phần còn lại của spec lớn). Commit `docs: thử tay giao tiếp, cập nhật hợp đồng app và bàn giao`.

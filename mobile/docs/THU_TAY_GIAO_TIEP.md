# Thử tay gọi và nhắn tin SeeVia

> Tài liệu dành cho người thử trên điện thoại Android thật. Ghi model máy, phiên bản Android và kết quả ở từng dòng; hành vi nền, Telecom và âm thanh có thể khác theo hãng.

## 1. Cài APK

APK debug nằm tại:

```text
mobile/app/build/outputs/apk/debug/app-debug.apk
```

Chép APK về máy tính đang cài ADB, rồi cài hoặc cập nhật:

```bash
scp user@build-host:/path/to/repo/mobile/app/build/outputs/apk/debug/app-debug.apk .
adb install -r app-debug.apk
```

Hoặc gửi APK cho điện thoại qua Drive/Zalo, tải về rồi mở tệp. Nếu Android hỏi, bật **Cho phép cài ứng dụng từ nguồn này** cho trình duyệt hoặc ứng dụng tệp vừa dùng.

## 2. Cấp quyền

Khi mở app, app hỏi các quyền runtime cần cho nhiều chức năng hiện có: **Điện thoại** (`CALL_PHONE`, `READ_PHONE_STATE`, `READ_CALL_LOG`, `ANSWER_PHONE_CALLS`), **Danh bạ** (`READ_CONTACTS`), **SMS** (`SEND_SMS`), **Vị trí** (`ACCESS_FINE_LOCATION`, `ACCESS_COARSE_LOCATION`) và **Micro** (`RECORD_AUDIO`). Android 13 trở lên hỏi thêm **Thông báo** (`POST_NOTIFICATIONS`). Micro cũng được hỏi khi bắt đầu gọi nếu chưa cấp. Từ Android 10, app có thể hỏi riêng quyền vị trí nền. App còn mở trang cài đặt để xin **Hiển thị trên ứng dụng khác** (`SYSTEM_ALERT_WINDOW`), bỏ tối ưu pin và bật thông báo toàn màn hình. Bluetooth `SCAN`/`CONNECT` được hỏi khi dùng chức năng cấp Wi-Fi cho kính trên Android 12 trở lên.

Để **gọi thoại qua mạng**, cần đăng nhập, có mạng, quyền **Micro** và nên bật **Thông báo** cùng **Thông báo toàn màn hình** để thấy cuộc gọi đến khi khoá máy. Để **nhắn tin qua mạng**, cần đăng nhập, mạng và nên bật thông báo tin nhắn; không cần micro. Quyền SIM, SMS và danh bạ không bắt buộc cho cuộc gọi/tin nhắn qua mạng; chúng phục vụ chức năng SIM/danh bạ cũ. `MANAGE_OWN_CALLS` là quyền Telecom khai trong app, không phải hộp thoại quyền runtime.

Nếu lời nhắc bị bỏ qua, mở **Cài đặt → Ứng dụng → SeeVia → Quyền** để bật Micro và Thông báo. Trên Android 14 trở lên kiểm tra **Cài đặt → Ứng dụng → SeeVia → Thông báo toàn màn hình** và cho phép. Bật thông báo cuộc gọi/tin nhắn và đặt mức ưu tiên phù hợp trong phần thông báo của SeeVia.

Để giảm việc hệ thống dừng app nền, vào **Cài đặt → Ứng dụng → SeeVia → Pin** và chọn **Không hạn chế** hoặc lựa chọn tương đương của hãng. Trên Xiaomi bật **Tự khởi động**; trên Oppo/Vivo bật **Tự khởi chạy/Cho phép chạy nền** nếu máy có mục này. Tên đường dẫn có thể khác theo ROM.

Ứng dụng đăng ký tài khoản gọi SeeVia với Telecom khi khởi động. Nếu máy có trang quản lý tài khoản gọi, kiểm tra SeeVia đang bật. Telecom có thể nhận cuộc gọi hoặc từ chối; khi từ chối app sẽ lùi về thông báo cuộc gọi toàn màn hình. Ghi lại kết quả theo hãng và đời máy.

## 3. Chuẩn bị

Cần hai tài khoản SeeVia đã đăng nhập: **chủ kính** và **người thân**. Chủ kính mời tài khoản người thân tại màn **Người thân**; tài khoản người thân mở lời mời và bấm **Chấp nhận**. Kiểm tra cả hai máy đã có mạng và đăng nhập đúng tài khoản.

Có thể dùng một điện thoại thật cho người thân, ghép với kính thật; hoặc thay đầu người thân bằng simulator. Từ thư mục `server/`, chạy script bằng Python 3.13 của môi trường dự án:

```bash
~/.venvs/seevia/bin/python scripts/relative_sim.py \
  --app-url http://127.0.0.1:18001 --port 18003 \
  --user '0900000001' --ab-log './ab.log' \
  --forward-sim http://127.0.0.1:18002/push
```

`--user` có thể lặp lại cho nhiều tài khoản. Tài khoản mới cần `--ab-log` để simulator đọc OTP từ log AB. Có thể thêm `--scenario <tep.json>` để đặt `incoming` thành `answer`, `reject` hoặc `ignore`; `--help` liệt kê đầy đủ tham số. Chạy AB với webhook theo cấu hình môi trường của bộ kiểm thử. Không đưa token hoặc tệp bí mật vào lệnh ghi lại hay gửi kèm báo cáo.

Để dựng cả kính giả lập cùng stack E2E, xem trợ giúp bằng:

```bash
cd visioncare-host-demo
~/.venvs/seevia/bin/python tools/e2e_calls.py --help
```

Script này dựng stack kiểm thử riêng cho AB, VH, điện thoại chủ kính, người thân giả lập và kính giả lập. Ví dụ theo trợ giúp: `python tools/e2e_calls.py --gpu 0`; có thể giới hạn ca bằng `--only call_mom call_nam`. Chỉ chạy trong môi trường test đã chuẩn bị, không dùng cổng/stack đang phục vụ người dùng.

## 4. Kịch bản thử

Mỗi ca ghi **hãng / model / phiên bản Android**, trạng thái pin và mạng nếu có ích. Ở cột cuối đánh dấu `☐` khi bắt đầu, `☑` khi đạt hoặc ghi ngắn gọn kết quả thực tế.

| # | Làm gì | Kỳ vọng | Đạt? |
|---:|---|---|:---:|
| 1 | Chủ kính mời người thân; người thân chấp nhận ở **Người thân**. | Lời mời chuyển thành đã kết nối; hai bên thấy đúng người và các nút phù hợp. | ☐ |
| 2 | Khoá màn hình người thân, đóng app khỏi màn gần đây; từ kính gọi tới. Lặp lại khi app đang mở. | Cuộc gọi đến hiện trên màn khoá; mỗi cuộc chỉ đổ chuông một lần, nút **Nghe** và **Từ chối** dùng được. | ☐ |
| 3 | Quan sát cuộc đến thông qua Telecom; thử thêm khi tắt tài khoản gọi SeeVia hoặc để Telecom từ chối. | Ghi lại Telecom nhận cuộc hay app lùi về thông báo toàn màn hình; ghi hãng, model và Android. | ☐ |
| 4 | Để cuộc gọi đến reo trên Android 9 trở lên; nếu có máy cũ hơn, lặp lại ở đó. | Android 9+ chuông lặp; máy cũ có thể chỉ phát một lần. Chuông tự tắt sau 45 giây nếu không có `call_ended`. | ☐ |
| 5 | Khi có cuộc gọi đến, bấm **Nghe** từ thông báo lúc máy khoá. Lặp lại với tai nghe Bluetooth đã kết nối. | Mở cuộc gọi, kết nối tiếng hai chiều; tai nghe/Bluetooth có thể trả lời hoặc phát tiếng theo khả năng máy. | ☐ |
| 6 | Trên Android 14 trở lên, bấm **Nghe** từ thông báo khi app ở nền/máy khoá rồi nói ở cả hai đầu. | Ghi riêng liệu app mở được micro và thu tiếng từ nền; nếu không, mở khoá/mở app và ghi thông báo hoặc lỗi hiện ra. | ☐ |
| 7 | Trong cuộc gọi, thử **Tắt mic**, **Mở mic**, **Loa ngoài**, **Dùng tai nghe**, rồi **Cúp máy**. | Mic tắt thì đầu kia không nghe tiếng điện thoại; trạng thái và tiếng định tuyến đổi đúng; cúp máy dừng tiếng và chuông. | ☐ |
| 8 | Gọi kính từ màn **Người thân** bằng **Gọi kính**; để kính bận, offline, hoặc không trả lời. | Màn gọi hiện trạng thái/kết quả dễ hiểu; có thể bấm **Huỷ cuộc gọi**; bận hoặc offline cho biết lý do. | ☐ |
| 9 | Trên điện thoại đang nghe, từ kính gửi tin; sau đó gửi tin từ **Hội thoại** để kính đọc. | Tin đến có thông báo hoặc hiện ngay khi hội thoại mở; lịch sử hai chiều đúng; tin gửi hiển thị **Đã gửi**, tin kính đã đọc hiển thị **Đã nghe**. | ☐ |
| 10 | Gửi tin `KHẨN CẤP` từ kính; thử khi hội thoại đang mở và khi app đóng. | Tin được làm nổi bật; khi hội thoại mở thì chèn trực tiếp thay vì tạo thông báo trùng. | ☐ |
| 11 | Tạo lời mời người thân đang chờ, bấm **Từ chối**; thử thêm thu hồi link đã kết nối. | Link không còn hoạt động; cuộc gọi/tin nhắn mới không dùng được với link đã thu hồi. | ☐ |
| 12 | Bật **Không làm phiền**, rồi gọi khẩn cấp từ kính. | Ghi xem chuông/thông báo khẩn cấp có vượt DND trên máy này không; quyền và chính sách DND của Android/ROM có thể ảnh hưởng. | ☐ |
| 13 | Đang có cuộc gọi SIM thật, để kính gọi tới SeeVia. Sau đó đang có cuộc gọi SeeVia, thử cuộc gọi internet khác. | Cuộc gọi SIM đang diễn ra không làm mất cuộc gọi SeeVia; cuộc VoIP của SeeVia không bị báo nhầm thành “có cuộc gọi đến” của SIM gửi lên kính. | ☐ |
| 14 | Để phiên đăng nhập hết hạn (hoặc thử bằng tài khoản/phiên test bị thu hồi), rồi nhận/gọi/nhắn. | Có thông báo **“Phiên đăng nhập hết hạn — mở app để đăng nhập lại”**; chạm thông báo mở app vào màn đăng nhập. | ☐ |
| 15 | Bật TalkBack; lần lượt khám phá các nút trên màn cuộc gọi, **Người thân**, **Hội thoại**. | TalkBack đọc đúng tên/hành động; nút cuộc gọi đủ lớn để chạm và không nhầm **Nghe** với **Từ chối**. | ☐ |
| 16 | Đang phát nhạc, gửi lệnh `emergency_call` đồng thời để một lệnh chậm như `music_play` đang chờ. | Lệnh khẩn cấp được xử lý ở làn FAST, không phải chờ lệnh SLOW. | ☐ |
| 17 | Tắt mạng ngay sau khi điện thoại nhận lệnh để report gửi thất bại; bật mạng lại. | Lệnh không chạy lần hai; report được thử lại trước lệnh kế tiếp hoặc bởi WorkManager khi có mạng. | ☐ |
| 18 | Bấm Gọi kính rồi bấm Huỷ ngay lập tức (và thử khi tắt mạng). | App không crash; màn hình về trạng thái kết thúc. | ☐ |
| 19 | Gọi kính nhưng kính không bắt máy, không có thông báo kết thúc tới. | Sau tối đa 45 s app tự kết thúc với "Không ai nghe máy"; không còn thông báo "đang gọi" trên thanh trạng thái. | ☐ |
| 20 | Kính gọi tới, không ai bấm gì, và tắt mạng điện thoại trong lúc đổ chuông. | Chuông và thông báo cuộc gọi đến tự tắt sau khoảng 45 s. | ☐ |
| 21 | Máy Android 8 trở xuống: kính gọi tới. | Chuông lặp lại tới khi nghe/từ chối, không chỉ kêu một lần. | ☐ |

## 5. Khi lỗi thì gửi gì

Tạo logcat ra tệp để gửi người hỗ trợ:

```bash
adb logcat -d | grep -E "SeeVia|youreyes|CallController|CommandExecution|FcmPushReceiver" > log.txt
```

Log app đã tránh ghi token, URL media và nội dung tin nhắn, nhưng hãy tự kiểm tra `log.txt` trước khi gửi. Xoá hoặc che số điện thoại, tên, nội dung riêng tư và mọi dữ liệu nhận dạng nếu có. Kèm model máy, phiên bản Android, thời điểm xảy ra, số thứ tự kịch bản và các bước vừa làm; không gửi tệp cấu hình hay token đăng nhập.

# Payload mẫu server -> điện thoại

Mỗi file là `data` mà server gửi cho điện thoại (FCM data message, hoặc body webhook
`WebhookDeliveryAdapter` ở E2E) cho một action. Chụp từ lần chạy thật: AB (webhook) ->
`scripts/phone_sim.py --capture-dir`. Chỉ thay các giá trị thay đổi theo lần chạy (id, thời điểm)
bằng giá trị cố định; không có token, push token hay số điện thoại nào trong payload.
Dùng cho test phía app (`CommandDispatcher.PushCommand.fromData` đọc đúng các khoá này).

Mọi giá trị đều là chuỗi (ràng buộc của FCM data).

| Trường | Ý nghĩa |
|---|---|
| `request_id` | UUID của thao tác; app dùng để chống chạy trùng và gửi lại trong report |
| `user_id` | id người dùng; app chép nguyên vào report |
| `device_id` | id kính đã ghép; app chép nguyên vào report |
| `action` | tên action (khoá `ActionType` trong `CommandModels.kt`) |
| `params_json` | tham số action dạng chuỗi JSON (rỗng/thiếu -> app coi như `{}`) |
| `issued_at` | lúc server tạo lệnh (ISO-8601, UTC) |
| `deadline_at` | hạn server dự định chờ kết quả (lưu ý: hiện server đặt = `issued_at` + 1 s) |
| `expires_at` | lúc lệnh hết hạn; sau đó server không còn chờ report |
| `requested_priority` | luôn `high` |

`params_json` theo action: `contact_call {name}`, `ride_quote {current_location, destination}`,
`ride_confirm {quote_id, confirm}`, `music_play {song, volume?}`, `music_stop {}`,
`music_volume {level | direction}`, `navigation_start {destination}`,
`navigation_stop {navigation_id}`, `emergency_call {}`.

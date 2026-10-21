# Kênh SIM (VH, nhánh gui-sim)

`CONTACT_CHANNEL=internet|sim` (config.py, mặc định internet, hành vi cũ giữ nguyên).
Kênh sim: `contact_call` -> `phone.handle_contact` (contact/call), `message_send` -> `handle_message_sms` (contact/sms qua `run_action`),
`message_read` -> "Chế độ SIM chưa đọc được tin nhắn."; `_lane_d` bỏ `_call_blocker`, vẫn hỏi tên/lời nhắn/đọc lại/cửa sổ huỷ.
Alias "em yêu" -> "em iu" (`_sim_name`, chỉ ở kênh sim). `visioncare_client.contact_sms` có sẵn nhưng handler đi qua `run_action`.
Lệch brief: CONTACT_NOT_FOUND nói "Không có {name} trong danh bạ." nhờ thay câu chung của `speak_result` trong handler;
MULTIPLE_CONTACTS_FOUND dùng câu có sẵn "Có nhiều người trùng tên..., vui lòng nói rõ hơn." (không liệt kê ứng viên, giống contact_call cũ).
Câu mới: "Đã gửi." (result_speech), SMS_SEND_FAILED, SIM_NO_READ đều trong STATIC_SPEECH. Test: tests/test_sim_channel.py.

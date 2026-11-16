"""Đổi kết quả cuối của một action thành câu nói tiếng Việt.

`202 Accepted` chỉ nói host đã nhận lệnh. Câu nói duy nhất người khiếm thị nghe
được phải phản ánh việc điện thoại có làm được hay không — "đang gọi cho Em iu"
khác hẳn "chưa gọi được, hãy bấm vào thông báo trên điện thoại", và trước đây cả
hai đều ra cùng một câu "đã gửi yêu cầu".
"""

_TIMEOUT_MESSAGE = "Điện thoại chưa trả lời. Bạn thử nói lại nhé."

# Mã lỗi nghiệp vụ -> câu nói. Mã không có ở đây rơi về câu chung của từng
# action, nên thiếu một mã mới không làm người dùng nghe mã lỗi thô.
_ERROR_SPEECH = {
    "GLASSES_DEVICE_NOT_LINKED": "Kính chưa ghép với tài khoản nào. Nhờ người thân ghép giúp bạn.",
    "DELIVERY_FAILED": "Chưa nối được với điện thoại. Bạn thử lại sau ít phút nhé.",
    "CONTACT_NOT_FOUND": "Không tìm thấy người này trong danh bạ.",
    "MULTIPLE_CONTACTS_FOUND": "Có nhiều người trùng tên. Bạn nói rõ cả họ và tên nhé.",
    "CONTACT_PERMISSION_DENIED": "Chưa đọc được danh bạ vì ứng dụng chưa có quyền.",
    "SMS_SEND_FAILED": "Chưa gửi được tin nhắn. Bạn thử lại nhé.",
    "CALL_PERMISSION_DENIED": "Chưa gọi được vì ứng dụng chưa có quyền gọi điện.",
    "SMS_PERMISSION_DENIED": "Chưa nhắn được vì ứng dụng chưa có quyền gửi tin nhắn.",
    "EMERGENCY_CONTACT_NOT_CONFIGURED": "Chưa cài người thân khẩn cấp.",
    "NO_ACTIVE_PLAYBACK": "Hiện không có bài hát nào đang phát, không cần dừng.",
    "SONG_NOT_FOUND": "Không tìm thấy bài này. Bạn nói lại tên bài nhé.",
    "PLAYBACK_STOP_FAILED": "Chưa dừng được nhạc. Bạn thử lại nhé.",
    "MUSIC_ACCOUNT_NOT_CONNECTED": "Chưa liên kết tài khoản nghe nhạc.",
    # Không nói "hết hạn": đo trên máy thật, mã này về khi tài khoản là gói MIỄN
    # PHÍ, chứ không phải gói trả phí vừa hết hạn. Ứng dụng nhạc mở lên bình
    # thường nhưng từ chối nạp đúng bài được yêu cầu, vì gói miễn phí không cho
    # phát một bài chỉ định. Nói "hết hạn" khiến người dùng đi gia hạn một thứ họ
    # chưa từng mua; nói rõ nguyên nhân thì họ biết phải nâng cấp.
    "SUBSCRIPTION_INACTIVE": "Tài khoản nhạc đang dùng gói miễn phí nên chưa phát được đúng bài này.",
    "CURRENT_LOCATION_UNAVAILABLE": "Không lấy được vị trí hiện tại.",
    "LOCATION_PERMISSION_DENIED": "Ứng dụng chưa được cấp quyền vị trí.",
    # Không đoán hộ một địa điểm gần đúng: nói thẳng là không tìm thấy để người
    # dùng đọc lại tên, còn hơn đưa họ tới nhầm chỗ.
    "INVALID_DESTINATION": "Không tìm thấy địa điểm này. Bạn nói lại tên địa điểm nhé.",
    "NAVIGATION_NOT_FOUND": "Không có chỉ đường nào đang chạy.",
    "NAVIGATION_ALREADY_STOPPED": "Chỉ đường đã dừng rồi.",
    "QUOTE_NOT_FOUND": "Không tìm thấy chuyến xe này. Bạn đặt lại giúp mình nhé.",
    "QUOTE_EXPIRED": "Chuyến xe này đã hết hạn. Bạn đặt lại nhé.",
    "NO_RIDE_AVAILABLE": "Hiện chưa có xe nào gần đây.",
    # Điện thoại không mở được ứng dụng ngoài (chưa cài / bị chặn): nói thẳng tên ứng dụng thay vì câu chung
    # "bấm vào thông báo" — không có thông báo nào để bấm.
    "RIDE_PROVIDER_ERROR": "Chưa mở được Grab trên điện thoại, có thể Grab chưa được cài.",
    "NAVIGATION_PROVIDER_ERROR": "Chưa mở được Google Maps trên điện thoại.",
    "RIDE_ACCOUNT_NOT_CONNECTED": "Chưa liên kết tài khoản đặt xe.",
    # App bỏ lệnh tới quá expires_at (FCM trễ) thay vì chạy lệnh cũ.
    "COMMAND_EXPIRED": "Điện thoại nhận lệnh quá muộn nên chưa làm. Bạn nói lại giúp tôi nhé.",
}

# Câu nói khi action thất bại mà mã lỗi không có trong bảng trên.
_GENERIC_FAILURE = {
    "contact_call": "Chưa gọi được. Bạn thử nói lại nhé.",
    "contact_sms": "Chưa gửi được tin nhắn. Bạn thử lại nhé.",
    "emergency_call": "Chưa gọi khẩn cấp được. Bạn thử nói lại ngay nhé.",
    "music_play": "Đã mở ứng dụng nhạc nhưng chưa phát được. Bạn thử nói lại tên bài nhé.",
    "music_stop": "Chưa dừng được nhạc. Bạn thử lại nhé.",
    "music_volume": "Chưa chỉnh được âm lượng. Bạn thử lại nhé.",
    "navigation_start": "Chưa chỉ đường được. Bạn thử nói lại địa điểm nhé.",
    "navigation_stop": "Chưa dừng được chỉ đường. Bạn thử lại nhé.",
    "ride_quote": "Chưa mở được ứng dụng đặt xe. Bạn thử nói lại nhé.",
    "ride_confirm": "Chưa gửi được yêu cầu đặt xe. Bạn thử nói lại nhé.",
}


# Câu nhắc cho các action mở ứng dụng khác trên điện thoại. Chúng đi qua
# notification full-screen-intent: màn hình tắt thì Android tự mở, còn khi người
# dùng đang cầm máy thì hệ điều hành cố ý chỉ hiện heads-up để không cắt ngang —
# lúc đó phải chạm. Cuộc gọi không nằm trong nhóm này vì đi qua
# TelecomManager.placeCall, không phải mở activity.
# Câu báo thành công không phụ thuộc kết quả trả về. Tách thành hằng số thay vì
# viết thẳng trong `_success_speech` để `pipeline/phrases.py` dựng sẵn audio cho
# chúng — đây là những câu được nói nhiều nhất trong cả demo.
# Dẫn đường là mở Google Maps chỉ đường đi bộ trên điện thoại (Maps tự đọc hướng dẫn), không phải bản đồ trên kính.
# Không đọc lại địa chỉ: câu đọc lại của làn D vừa nói nó một hai giây trước. Không bảo "mở thông báo": người dùng
# nghe qua kính, điện thoại tự mở Maps (full-screen intent).
_NAV_SENT = "Đã gửi chỉ đường đi bộ sang Google Maps trên điện thoại."
# Maps là ứng dụng riêng: điện thoại chỉ ngừng theo dõi lộ trình, không tắt được hướng dẫn đang chạy trong Maps.
# Nói đúng như vậy thay vì "đã dừng" (báo bừa thành công).
_NAV_STOPPED = "Đã dừng chỉ đường. Google Maps trên điện thoại có thể vẫn đang chạy."
_MUSIC_STOPPED = "Đã dừng phát nhạc."
_VOLUME_CHANGED = "Đã thay đổi âm lượng."
_RIDE_CANCELLED = "Đã hủy chuyến xe."
_DONE = "Đã thực hiện xong."
_SMS_SENT = "Đã gửi."
# Action thất bại mà cả mã lỗi lẫn tên action đều không có câu riêng.
_UNSPECIFIED_FAILURE = "Chưa làm được. Bạn thử lại nhé."

# Cố ý không đọc giá, loại xe hay thời gian chờ. Trên Grab thật, giá chỉ hiện
# sau khi người dùng xác nhận điểm đón, mà app không đi qua được bước xác nhận
# đó — nên mọi con số ở đây là ước lượng của server, đọc lên là nói một cái giá
# không ai cam kết.
#
# Một bước: Grab không có API đặt xe công khai, chuyến chỉ thành khi người dùng bấm Đặt xe trong Grab. Trước đây câu
# này bảo "nói xác nhận để đặt xe" rồi `ride_confirm` chỉ mở lại Grab và nói "Đã gửi yêu cầu đặt xe" — thêm một lượt
# hỏi mà không làm gì thêm, và báo bừa là đã đặt.
_RIDE_APP_OPENED = "Đã mở Grab, điểm đón là vị trí hiện tại. Bấm Đặt xe trong Grab."
_RIDE_REQUESTED = "Đã mở lại Grab cho chuyến này. Bấm Đặt xe trong Grab."

# Câu cố định của module này, để `pipeline/phrases.py` dựng sẵn audio.
STATIC_SPEECH: tuple[str, ...] = (
    _TIMEOUT_MESSAGE,
    _NAV_SENT,
    _NAV_STOPPED,
    _MUSIC_STOPPED,
    _VOLUME_CHANGED,
    _RIDE_CANCELLED,
    _RIDE_APP_OPENED,
    _RIDE_REQUESTED,
    _DONE,
    _SMS_SENT,
    _UNSPECIFIED_FAILURE,
    *_ERROR_SPEECH.values(),
    *_GENERIC_FAILURE.values(),
)


def _success_speech(operation: str, result: dict) -> str:
    if operation == "contact_call":
        return f"Đang gọi cho {result.get('contact_name', 'người liên hệ')}."

    if operation == "contact_sms":
        return _SMS_SENT

    if operation == "emergency_call":
        sms = "đã gửi tin nhắn vị trí" if result.get("sms_sent") else "chưa gửi được tin nhắn vị trí"
        return f"Đang gọi khẩn cấp tới {result.get('contact', 'số đã cài')}, {sms}."

    if operation == "music_play":
        title = result.get("title") or "bài hát"
        artist = result.get("artist")
        if artist and artist != "Unknown":
            return f"Đã tìm được bài {title} của {artist}, đang phát."
        return f"Đã tìm được bài {title}, đang phát."

    if operation == "navigation_start":
        return _NAV_SENT

    if operation == "music_stop":
        return _MUSIC_STOPPED

    if operation == "music_volume":
        level = result.get("level")
        if isinstance(level, int):
            return f"Âm lượng hiện ở mức {level} phần trăm."
        return _VOLUME_CHANGED

    if operation == "navigation_stop":
        return _NAV_STOPPED

    if operation == "ride_quote":
        return _RIDE_APP_OPENED

    if operation == "ride_confirm":
        if result.get("ride_status") == "cancelled":
            return _RIDE_CANCELLED
        return _RIDE_REQUESTED

    return _DONE


def error_speech(code: str) -> str:
    """Câu nói cho một mã lỗi nghiệp vụ (để nơi khác nhận ra câu đó khi nó được đọc)."""
    return _ERROR_SPEECH[code]


def speak_result(operation: str, data: dict | None) -> str:
    """Câu nói cuối cùng cho một action, dựa trên trạng thái thật từ điện thoại.

    `data` là phần `data` của `GET /api/v1/requests/{id}`, hoặc `None` khi chờ
    hết giờ mà request vẫn `processing`.
    """
    if data is None:
        return _TIMEOUT_MESSAGE

    state = data.get("request_state")
    if state == "succeeded":
        result = data.get("result")
        return _success_speech(operation, result if isinstance(result, dict) else {})

    if state == "timed_out":
        return _TIMEOUT_MESSAGE

    error = data.get("error") or {}
    code = error.get("code", "")
    if code in _ERROR_SPEECH:
        return _ERROR_SPEECH[code]
    return _GENERIC_FAILURE.get(operation, _UNSPECIFIED_FAILURE)

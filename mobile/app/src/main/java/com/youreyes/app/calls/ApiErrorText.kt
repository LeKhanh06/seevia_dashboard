package com.youreyes.app.calls

import com.youreyes.app.network.ApiResult

/**
 * Vietnamese text for a failed care/message API call (protocol 7.1, 7.4). The server's own
 * message is English and for developers; people never see it.
 */
fun apiErrorVi(result: ApiResult<*>): String = when (result) {
    is ApiResult.Ok -> ""
    ApiResult.Unauthorized -> "Phiên đăng nhập hết hạn — mở app để đăng nhập lại"
    is ApiResult.Network -> "Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại."
    is ApiResult.Failed -> when (result.code) {
        "USER_NOT_FOUND" -> "Số này chưa có tài khoản SeeVia"
        "CARE_LINK_CONFLICT" -> "Đã mời hoặc đã là người thân"
        "CARE_LINK_NOT_FOUND" -> "Không tìm thấy lời mời này. Hãy tải lại danh sách."
        "MESSAGE_NOT_ALLOWED" -> "Bạn chưa được phép nhắn cho người này"
        "INVALID_REQUEST" -> "Thông tin chưa hợp lệ. Hãy kiểm tra lại."
        "REQUEST_ID_CONFLICT" -> "Tin nhắn bị trùng mã, hãy gửi lại"
        else -> if (result.httpStatus >= 500) "Máy chủ đang gặp lỗi, thử lại sau" else "Không thực hiện được, thử lại sau"
    }
}

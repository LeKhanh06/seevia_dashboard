package com.youreyes.app.ui.readiness

/** One thing a server command depends on. Mirrors the keys `capabilities_get` reports. */
enum class ReadinessKey {
    NOTIFICATION_ACCESS,
    BATTERY_OPTIMIZATION,
    OVERLAY,
    LOCATION_SWITCH,
    BACKGROUND_LOCATION,
    CONTACTS,
    SMS,
    CALL,
    PHONE_STATE,
    EMERGENCY_CONTACT,
    BLUETOOTH,
}

/** What the phone currently grants; filled by [ReadinessChecker], read by [ReadinessUiState.build]. */
data class ReadinessSnapshot(
    val notificationAccess: Boolean,
    val batteryOptimizationOff: Boolean,
    val overlay: Boolean,
    val locationSwitchOn: Boolean,
    val backgroundLocation: Boolean,
    val contacts: Boolean,
    val sms: Boolean,
    val call: Boolean,
    val phoneState: Boolean,
    val emergencyContactSet: Boolean,
    val bluetooth: Boolean,
)

data class ReadinessItem(
    val key: ReadinessKey,
    val title: String,
    /** Why the server needs it, in the words a wearer would use. */
    val reason: String,
    val ready: Boolean,
)

data class ReadinessUiState(val items: List<ReadinessItem> = emptyList()) {
    val total: Int get() = items.size
    val readyCount: Int get() = items.count { it.ready }
    val missing: List<ReadinessItem> get() = items.filterNot { it.ready }
    val allReady: Boolean get() = items.isNotEmpty() && missing.isEmpty()

    val summary: String
        get() = when {
            items.isEmpty() -> "Đang kiểm tra..."
            allReady -> "Đã sẵn sàng nhận lệnh"
            else -> "Còn ${missing.size} mục cần bật"
        }

    companion object {
        /** Missing rows first: the person came here to fix something, not to admire what works. */
        fun build(snapshot: ReadinessSnapshot): ReadinessUiState {
            val items = listOf(
                ReadinessItem(
                    ReadinessKey.NOTIFICATION_ACCESS,
                    "Truy cập thông báo",
                    "Để kính điều khiển nhạc Spotify trên điện thoại.",
                    snapshot.notificationAccess,
                ),
                ReadinessItem(
                    ReadinessKey.LOCATION_SWITCH,
                    "Bật vị trí của máy",
                    "Để báo vị trí hiện tại và dẫn đường. Công tắc Vị trí của máy đang tắt thì không lấy được tọa độ.",
                    snapshot.locationSwitchOn,
                ),
                ReadinessItem(
                    ReadinessKey.BATTERY_OPTIMIZATION,
                    "Không hạn chế pin",
                    "Để máy vẫn nhận lệnh từ kính khi tắt màn hình.",
                    snapshot.batteryOptimizationOff,
                ),
                ReadinessItem(
                    ReadinessKey.OVERLAY,
                    "Hiện trên ứng dụng khác",
                    "Để màn hình cuộc gọi đến hiện lên ngay cả khi đang dùng app khác.",
                    snapshot.overlay,
                ),
                ReadinessItem(
                    ReadinessKey.EMERGENCY_CONTACT,
                    "Người thân khẩn cấp",
                    "Để gọi và nhắn tin khẩn cấp khi bấm SOS.",
                    snapshot.emergencyContactSet,
                ),
                ReadinessItem(
                    ReadinessKey.BACKGROUND_LOCATION,
                    "Vị trí luôn luôn",
                    "Để lấy vị trí khi app chạy nền, lúc màn hình tắt.",
                    snapshot.backgroundLocation,
                ),
                ReadinessItem(
                    ReadinessKey.CONTACTS,
                    "Danh bạ",
                    "Để gọi người thân theo tên.",
                    snapshot.contacts,
                ),
                ReadinessItem(
                    ReadinessKey.CALL,
                    "Gọi điện",
                    "Để thực hiện cuộc gọi theo lệnh của kính.",
                    snapshot.call,
                ),
                ReadinessItem(
                    ReadinessKey.SMS,
                    "Nhắn tin SMS",
                    "Để gửi tin nhắn khẩn cấp.",
                    snapshot.sms,
                ),
                ReadinessItem(
                    ReadinessKey.PHONE_STATE,
                    "Trạng thái cuộc gọi",
                    "Để biết khi có cuộc gọi đến và báo cho kính.",
                    snapshot.phoneState,
                ),
                ReadinessItem(
                    ReadinessKey.BLUETOOTH,
                    "Bluetooth",
                    "Để cài đặt Wi-Fi cho kính bằng điện thoại.",
                    snapshot.bluetooth,
                ),
            )
            return ReadinessUiState(items.sortedBy { it.ready })
        }
    }
}

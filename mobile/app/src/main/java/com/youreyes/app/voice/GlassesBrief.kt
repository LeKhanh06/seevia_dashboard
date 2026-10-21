package com.youreyes.app.voice

import com.youreyes.app.network.GlassesStatus

/**
 * The glasses in one short phrase, for the home-screen tile and for the voice command
 * "kính thế nào". The server reports whether the glasses are online and on which Wi-Fi; it does
 * not report their battery, so the app says so instead of inventing a number.
 */
data class GlassesBrief(val state: State, val wifi: String? = null) {

    enum class State { NOT_PAIRED, CHECKING, ONLINE, OFFLINE, UNKNOWN_SERIAL, UNAVAILABLE }

    /** Short status shown on the tile. */
    val title: String
        get() = when (state) {
            State.NOT_PAIRED -> "Chưa liên kết"
            State.CHECKING -> "Đang kiểm tra…"
            State.ONLINE -> "Đang hoạt động"
            State.OFFLINE -> "Đang tắt hoặc mất mạng"
            State.UNKNOWN_SERIAL -> "Chưa thấy kính kết nối"
            State.UNAVAILABLE -> "Chưa kiểm tra được"
        }

    /** What the voice assistant says. */
    fun spoken(): String = when (state) {
        State.NOT_PAIRED -> "Bạn chưa liên kết kính nào. Nói “mở kính” để liên kết."
        State.CHECKING -> "Đang kiểm tra kính."
        State.ONLINE -> "Kính đang hoạt động" + (wifi?.let { ", đang dùng Wi-Fi $it" } ?: "") + ". $NO_BATTERY"
        State.OFFLINE -> "Kính đang tắt hoặc mất mạng. Hãy bật kính và kiểm tra Wi-Fi. $NO_BATTERY"
        State.UNKNOWN_SERIAL -> "Máy chủ chưa thấy kính này kết nối lần nào. Hãy kiểm tra lại số máy in trên gọng kính."
        State.UNAVAILABLE -> "Chưa kiểm tra được tình trạng kính. Kiểm tra mạng điện thoại rồi thử lại."
    }

    companion object {
        const val NO_BATTERY = "App chưa nhận được thông tin pin từ kính."

        fun from(pairedId: String, status: GlassesStatus?, fetchFailed: Boolean): GlassesBrief = when {
            pairedId.isBlank() -> GlassesBrief(State.NOT_PAIRED)
            fetchFailed || status == null -> GlassesBrief(State.UNAVAILABLE)
            !status.known -> GlassesBrief(State.UNKNOWN_SERIAL)
            status.online -> GlassesBrief(State.ONLINE, status.wifiSsid)
            else -> GlassesBrief(State.OFFLINE, status.wifiSsid)
        }
    }
}

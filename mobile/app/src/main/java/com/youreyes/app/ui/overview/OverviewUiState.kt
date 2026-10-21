package com.youreyes.app.ui.overview

import com.youreyes.app.command.CommandRecord
import com.youreyes.app.command.PendingReportRecord

data class OverviewUiState(
    val serverUrl: String = OverviewViewModel.DEFAULT_SERVER_URL,
    val bearerToken: String = OverviewViewModel.DEFAULT_DEVICE_BEARER_TOKEN,
    val userId: String = "user-1",
    val deviceId: String = "device-1",
    val emergencyContact: String = "",
    val pushToken: String = "push-token-demo-123",
    val isLoading: Boolean = false,
    val isRegistered: Boolean = false,
    val registrationMessage: String = "Chưa kết nối máy chủ",
    val commands: List<CommandRecord> = emptyList(),
    val pendingReports: List<PendingReportRecord> = emptyList()
) {
    /** The last attempt to register this phone failed (as opposed to never having been tried). */
    val registrationFailed: Boolean
        get() = registrationMessage.startsWith(OverviewViewModel.REGISTER_FAILED)

    val serverConfigured: Boolean
        get() = serverUrl.isNotBlank()

    val deviceRegistered: Boolean
        get() = isRegistered

    val deliveryMode: String
        get() = "HTTP / SQLite"

    val isReady: Boolean
        get() = serverConfigured && deviceRegistered

    /**
     * The phone has never reached the server, and the build already carries everything a
     * registration needs. Without this the person has to find the developer form and tap
     * "register" before the server can push anything to the phone.
     */
    val shouldAutoRegister: Boolean
        get() = !isRegistered &&
            !isLoading &&
            serverUrl.isNotBlank() &&
            bearerToken.isNotBlank() &&
            userId.isNotBlank() &&
            deviceId.isNotBlank()

    companion object {
        /** A demo build can name its glasses; a person-made pairing is never overwritten. */
        fun shouldAutoLinkGlasses(configuredId: String, pairedId: String): Boolean =
            configuredId.isNotBlank() && pairedId.isBlank()
    }
}

package com.youreyes.app.ui.overview

import android.app.Application
import android.content.Context
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.google.firebase.messaging.FirebaseMessaging
import com.youreyes.app.BuildConfig
import com.youreyes.app.calls.SessionApi
import com.youreyes.app.core.AppConfig
import com.youreyes.app.voice.AppVoiceEnv
import com.youreyes.app.voice.GlassesBrief
import com.youreyes.app.voice.VoiceContact
import com.youreyes.app.network.DeviceRegisterPayload
import com.youreyes.app.network.DeviceApiClient
import com.youreyes.app.network.GlassesLinkPayload
import com.youreyes.app.ui.glasses.GlassesLinkViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await

/** What the daily-use buttons on Home say underneath their titles. */
data class QuickState(
    val glasses: GlassesBrief = GlassesBrief(GlassesBrief.State.CHECKING),
    /** People whose glasses this account can call or message. */
    val contacts: List<VoiceContact> = emptyList(),
    val loggedIn: Boolean = false,
) {
    val callDetail: String
        get() = when {
            !loggedIn -> "Cần đăng nhập"
            contacts.isEmpty() -> "Chưa có người thân"
            contacts.size == 1 -> contacts.first().name
            else -> "${contacts.size} người thân"
        }

    val messageDetail: String
        get() = when {
            !loggedIn -> "Cần đăng nhập"
            contacts.isEmpty() -> "Chưa có người thân"
            contacts.size == 1 -> "Nhắn cho ${contacts.first().name}"
            else -> "Chọn người để nhắn"
        }
}

class OverviewViewModel(application: Application) : AndroidViewModel(application) {

    private val voiceEnv = AppVoiceEnv(application) {}

    private val _quick = MutableStateFlow(QuickState(loggedIn = SessionApi.hasSession(application)))
    val quick: StateFlow<QuickState> = _quick.asStateFlow()

    /** Reloads who can be called and how the glasses are doing; call when Home is shown. */
    fun refreshQuick() {
        val loggedIn = SessionApi.hasSession(getApplication())
        _quick.update { it.copy(loggedIn = loggedIn) }
        viewModelScope.launch {
            val brief = voiceEnv.glassesBrief()
            _quick.update { it.copy(glasses = brief) }
        }
        if (loggedIn) {
            viewModelScope.launch {
                val contacts = voiceEnv.contacts()
                _quick.update { it.copy(contacts = contacts) }
            }
        } else {
            _quick.update { it.copy(contacts = emptyList()) }
        }
    }

    private val prefs = AppConfig.prefs(application)

    private val _uiState = MutableStateFlow(loadInitialState())
    val uiState: StateFlow<OverviewUiState> = _uiState.asStateFlow()

    init {
        // The demo build carries the server and token, so the person is never asked to
        // register. A failed attempt (offline) is simply retried on the next launch.
        if (_uiState.value.shouldAutoRegister) register()
    }

    private fun loadInitialState(): OverviewUiState {
        // Provisioned by the backend for this demo build; the user never has to type
        // these in by hand — only user_id/device_id/emergency contact are theirs to set.
        val savedUrl     = prefs.getString(AppConfig.KEY_BASE_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        val savedUserId  = prefs.getString(AppConfig.KEY_USER_ID, "user-100") ?: "user-100"
        val savedDevice  = prefs.getString(AppConfig.KEY_DEVICE_ID, "device-100") ?: "device-100"
        val savedToken   = prefs.getString(AppConfig.KEY_BEARER_TOKEN, DEFAULT_DEVICE_BEARER_TOKEN) ?: DEFAULT_DEVICE_BEARER_TOKEN
        val savedEmergencyContact = prefs.getString(AppConfig.KEY_EMERGENCY_CONTACT, "") ?: ""
        // Only a successful register() stores the push token, so it marks real registration.
        val registered   = !prefs.getString(AppConfig.KEY_FCM_TOKEN, null).isNullOrBlank() &&
            prefs.contains(AppConfig.KEY_BASE_URL)
        return OverviewUiState(
            serverUrl = savedUrl,
            bearerToken = savedToken,
            userId = savedUserId,
            deviceId = savedDevice,
            emergencyContact = savedEmergencyContact,
            isRegistered = registered,
            registrationMessage = if (registered) "Đã kết nối máy chủ" else "Chưa kết nối máy chủ",
        )
    }

    /**
     * Re-reads `user_id` from SharedPreferences. Call when this screen is re-entered:
     * [com.youreyes.app.ui.auth.AuthViewModel] may have overwritten it with the real
     * logged-in `public_user_id` after this ViewModel's own `loadInitialState()` ran
     * (that constructor call happens once and is cached for the Activity's lifetime,
     * so it would otherwise miss a login that happened on a different tab).
     */
    fun refreshUserId() {
        val saved = prefs.getString(AppConfig.KEY_USER_ID, null) ?: return
        _uiState.update { if (it.userId != saved) it.copy(userId = saved) else it }
    }

    fun onServerUrlChange(url: String) = _uiState.update { it.copy(serverUrl = url) }
    fun onUserIdChange(id: String)     = _uiState.update { it.copy(userId = id) }
    fun onDeviceIdChange(id: String)   = _uiState.update { it.copy(deviceId = id) }
    fun onBearerTokenChange(t: String) = _uiState.update { it.copy(bearerToken = t) }
    /** Saved at once: the SOS button and the voice command read it from preferences, not from this screen. */
    fun onEmergencyContactChange(v: String) {
        _uiState.update { it.copy(emergencyContact = v) }
        prefs.edit().putString(AppConfig.KEY_EMERGENCY_CONTACT, v.trim()).apply()
    }

    /** Fills user_id/device_id with fresh random-suffixed defaults; still editable afterward. */
    fun generateNewIds() {
        val suffix = (100..999).random()
        _uiState.update { it.copy(userId = "user-$suffix", deviceId = "device-$suffix") }
    }

    fun register() {
        _uiState.update { it.copy(isLoading = true, registrationMessage = "Đang kết nối máy chủ…") }
        viewModelScope.launch(Dispatchers.IO) {
            try {
                // Without a real FCM token the server could never push to this phone.
                val fcmToken = try {
                    FirebaseMessaging.getInstance().token.await()
                } catch (e: Exception) {
                    Log.w(TAG, "FCM token unavailable: ${e.message}")
                    _uiState.update {
                        it.copy(
                            isLoading = false,
                            isRegistered = false,
                            registrationMessage = "$REGISTER_FAILED Điện thoại cần Google Play Services và mạng để nhận lệnh từ kính.",
                        )
                    }
                    return@launch
                }

                val state = _uiState.value
                val result = DeviceApiClient().registerDevice(
                    baseUrl = state.serverUrl.trimEnd('/'),
                    bearerToken = state.bearerToken,
                    payload = DeviceRegisterPayload(
                        userId   = state.userId.trim(),
                        deviceId = state.deviceId.trim(),
                        platform = "android",
                        pushToken = fcmToken,
                    ),
                )

                if (result.isSuccess) {
                    // Persist for FcmPushReceiver auto re-register
                    prefs.edit()
                        .putString(AppConfig.KEY_BASE_URL,      state.serverUrl.trimEnd('/'))
                        .putString(AppConfig.KEY_USER_ID,       state.userId.trim())
                        .putString(AppConfig.KEY_DEVICE_ID,     state.deviceId.trim())
                        .putString(AppConfig.KEY_FCM_TOKEN,     fcmToken)
                        .putString(AppConfig.KEY_BEARER_TOKEN,  state.bearerToken)
                        .putString(AppConfig.KEY_EMERGENCY_CONTACT, state.emergencyContact.trim())
                        .apply()

                    linkDemoGlassesIfConfigured(state.serverUrl.trimEnd('/'), state.bearerToken, state.userId.trim())

                    _uiState.update {
                        it.copy(
                            isLoading = false,
                            isRegistered = true,
                            pushToken = fcmToken,
                            registrationMessage = "Đã kết nối máy chủ. Kính có thể gửi lệnh tới điện thoại.",
                        )
                    }
                } else {
                    _uiState.update {
                        it.copy(isLoading = false, registrationMessage = "$REGISTER_FAILED Máy chủ chưa nhận kết nối từ điện thoại. Thử lại sau ít phút.")
                    }
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(isLoading = false, registrationMessage = "$REGISTER_FAILED Không có kết nối mạng. Kiểm tra mạng rồi thử lại.")
                }
            }
        }
    }

    /**
     * Pairs the glasses a demo build names, so the server can route requests to this phone
     * without anyone typing a serial. Runs on the registration coroutine (already off the
     * main thread); a failure is logged and leaves the manual pairing screen as the way out.
     */
    private fun linkDemoGlassesIfConfigured(baseUrl: String, bearerToken: String, userId: String) {
        val pairedId = prefs.getString(AppConfig.KEY_PAIRED_GLASSES_ID, "") ?: ""
        if (!OverviewUiState.shouldAutoLinkGlasses(BuildConfig.DEMO_GLASSES_DEVICE_ID, pairedId)) return

        val glassesId = GlassesLinkViewModel.normalizedDeviceId(BuildConfig.DEMO_GLASSES_DEVICE_ID)
        DeviceApiClient().linkGlassesDevice(baseUrl, bearerToken, GlassesLinkPayload(userId = userId, deviceId = glassesId))
            .onSuccess { prefs.edit().putString(AppConfig.KEY_PAIRED_GLASSES_ID, glassesId).apply() }
            .onFailure { Log.w(TAG, "Demo glasses link failed: ${it.message}") }
    }

    companion object {
        private const val TAG = "OverviewViewModel"

        /** Prefix of every failed-registration message; the Home screen shows a retry button for it. */
        const val REGISTER_FAILED = "Chưa kết nối được."

        // Build-time defaults from local.properties, so the demo user only taps "Đăng ký".
        const val DEFAULT_SERVER_URL = BuildConfig.SERVER_URL
        const val DEFAULT_DEVICE_BEARER_TOKEN = BuildConfig.DEVICE_API_TOKEN
    }
}

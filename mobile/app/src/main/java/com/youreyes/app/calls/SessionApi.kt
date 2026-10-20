package com.youreyes.app.calls

import android.content.Context
import com.youreyes.app.core.AppConfig
import com.youreyes.app.network.ApiResult
import com.youreyes.app.network.SessionApiClient
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * One place that builds [SessionApiClient] from what the login saved (same `base_url` the login
 * uses, access token in [AppConfig.KEY_ACCESS_TOKEN]) and applies the 401 rule: clear the token,
 * tell the user. Blocking: call from a background thread only.
 */
object SessionApi {
    val http: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .callTimeout(20, TimeUnit.SECONDS)
            .build()
    }

    /** For a WebSocket: no read timeout (silence is normal), pings detect a dead link. */
    val media: OkHttpClient by lazy {
        http.newBuilder().readTimeout(0, TimeUnit.MILLISECONDS).callTimeout(0, TimeUnit.MILLISECONDS)
            .pingInterval(20, TimeUnit.SECONDS).build()
    }

    private val quick: OkHttpClient by lazy { http.newBuilder().callTimeout(3, TimeUnit.SECONDS).build() }

    fun hasSession(context: Context): Boolean = accessToken(context) != null

    private fun accessToken(context: Context): String? =
        AppConfig.prefs(context).getString(AppConfig.KEY_ACCESS_TOKEN, null)?.takeIf { it.isNotBlank() }

    /** The signed-in user's `public_user_id` (saved by the login), or "". */
    fun myUserId(context: Context): String =
        AppConfig.prefs(context).getString(AppConfig.KEY_USER_ID, "").orEmpty()

    /**
     * Runs [block] with a client for the current session. [quick] caps the whole request at 3 s
     * (the "is this call still ringing?" check). No token or no server address: [ApiResult.Unauthorized]
     * / [ApiResult.Network] without any request being made.
     */
    fun <T> run(context: Context, quick: Boolean = false, block: (SessionApiClient) -> ApiResult<T>): ApiResult<T> {
        val prefs = AppConfig.prefs(context)
        val token = accessToken(context) ?: return ApiResult.Unauthorized
        val base = prefs.getString(AppConfig.KEY_BASE_URL, null)?.takeIf { it.isNotBlank() }
            ?: return ApiResult.Network(IllegalStateException("No server address"))
        val result = block(SessionApiClient(base, token, if (quick) this.quick else http))
        if (result is ApiResult.Unauthorized) SessionExpiry.onUnauthorized(context)
        return result
    }
}

object SessionExpiry {
    /** A 401: the token is dead. Forget it and ask the user to sign in again (no refresh flow yet). */
    fun onUnauthorized(context: Context) {
        val app = context.applicationContext
        AppConfig.prefs(app).edit().remove(AppConfig.KEY_ACCESS_TOKEN).apply()
        CallNotifications.showSessionExpired(app)
    }
}

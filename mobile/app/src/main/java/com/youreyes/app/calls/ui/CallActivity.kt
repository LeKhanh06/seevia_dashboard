package com.youreyes.app.calls.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.youreyes.app.calls.CallController
import com.youreyes.app.calls.CallState
import com.youreyes.app.ui.theme.AppDemoTheme
import kotlinx.coroutines.launch

/**
 * The call screen. Shows over the lock screen and turns the screen on (incoming calls), keeps it
 * on during the call, and closes itself when the controller is idle again. Holds no call logic:
 * it asks for the microphone, then tells [CallController].
 */
class CallActivity : ComponentActivity() {

    private var notice by mutableStateOf("")
    private var afterMic: (() -> Unit)? = null

    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val action = afterMic
        afterMic = null
        if (granted) {
            notice = ""
            action?.invoke()
        } else {
            notice = "Cần cho phép micro để nói chuyện. Hãy bật micro cho SeeVia trong Cài đặt."
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        showOverLockScreen()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        setContent {
            AppDemoTheme {
                val state by CallController.state.collectAsState()
                val extras by CallController.extras.collectAsState()
                CallScreen(
                    state = state,
                    extras = extras,
                    notice = notice,
                    onAnswer = ::answerWithMic,
                    onReject = CallController::reject,
                    onHangup = CallController::hangup,
                    onMute = CallController::mute,
                    onSpeaker = CallController::speaker,
                    onClose = CallController::dismiss,
                )
            }
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.CREATED) {
                CallController.state.collect { if (it == CallState.Idle) finish() }
            }
        }
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    override fun onStart() {
        super.onStart()
        CallController.uiVisible = true
    }

    override fun onStop() {
        CallController.uiVisible = false
        super.onStop()
    }

    private fun handleIntent(intent: Intent) {
        if (intent.getBooleanExtra(EXTRA_ANSWER, false)) {
            intent.removeExtra(EXTRA_ANSWER)
            answerWithMic()
        }
    }

    private fun answerWithMic() {
        withMic { CallController.answer() }
    }

    private fun withMic(action: () -> Unit) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            action()
        } else {
            afterMic = action
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    @Suppress("DEPRECATION")
    private fun showOverLockScreen() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON,
            )
        }
    }

    companion object {
        const val EXTRA_ANSWER = "answer"

        /** [answer] = true: open and answer straight away (the notification's Nghe button). */
        fun intent(context: Context, answer: Boolean = false): Intent =
            Intent(context, CallActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra(EXTRA_ANSWER, answer)
    }
}

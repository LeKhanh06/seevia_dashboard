package com.youreyes.app.voice

import com.youreyes.app.ui.navigation.Routes

/** Where each screen a voice command can open lives. */
object VoiceRoutes {
    fun routeFor(screen: VoiceScreen): String = when (screen) {
        VoiceScreen.HOME -> Routes.HOME
        VoiceScreen.FEATURES -> Routes.FEATURES
        VoiceScreen.SAFETY -> Routes.SAFETY
        VoiceScreen.PROFILE -> Routes.PROFILE
        VoiceScreen.CARE -> Routes.CARE
        VoiceScreen.GLASSES -> Routes.GLASSES
        VoiceScreen.DISPLAY -> Routes.DISPLAY
        VoiceScreen.GUIDE -> Routes.GUIDE
        VoiceScreen.ACTIVITY_LOG -> Routes.ACTIVITY_LOG
        VoiceScreen.SUPPORT -> Routes.SUPPORT
        VoiceScreen.AUTH -> Routes.AUTH
        VoiceScreen.READINESS -> Routes.READINESS
    }
}

package com.youreyes.app

import android.app.Application
import com.youreyes.app.calls.CallNotifications
import com.youreyes.app.calls.telecom.TelecomBridge
import com.youreyes.app.messages.MessageNotifications
import com.youreyes.app.telephony.IncomingCallMonitor

class YourEyesApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        IncomingCallMonitor.start(this)
        CallNotifications.ensureChannels(this)
        MessageNotifications.ensureChannels(this)
        TelecomBridge.registerAccount(this)
    }
}

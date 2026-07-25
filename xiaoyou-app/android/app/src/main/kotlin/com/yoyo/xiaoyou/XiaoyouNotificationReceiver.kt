package com.yoyo.xiaoyou

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Restores the user-enabled background delivery service after a reboot or
 * package update. The service itself restores its persisted connection config.
 */
class XiaoyouNotificationReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        Log.i(
            "XiaoyouNotify",
            "Restoring background delivery after ${intent?.action ?: "system restart"}",
        )
        XiaoyouNotificationService.startIfEnabled(context)
    }
}

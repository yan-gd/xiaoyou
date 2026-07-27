package com.yoyo.xiaoyou

import android.content.Context
import android.content.Intent
import android.util.Log
import com.vivo.push.model.UPSNotificationMessage
import com.vivo.push.sdk.OpenClientPushMessageReceiver

class XiaoyouVivoPushReceiver : OpenClientPushMessageReceiver() {
    override fun onReceiveRegId(context: Context, regId: String) {
        Log.i("XiaoyouSystemPush", "vivo registration id refreshed")
        XiaoyouSystemPush.saveRegistration(context, regId)
    }

    override fun onNotificationMessageClicked(
        context: Context,
        message: UPSNotificationMessage,
    ) {
        context.startActivity(
            Intent(context, MainActivity::class.java).apply {
                flags =
                    Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_SINGLE_TOP or
                        Intent.FLAG_ACTIVITY_CLEAR_TOP
                putExtra("xiaoyou_action_id", message.params["action_id"])
            },
        )
    }

    override fun onForegroundMessageArrived(message: UPSNotificationMessage) {
        // The foreground Flutter event stream renders this message directly.
        // Avoid a second local notification while the conversation is visible.
    }
}

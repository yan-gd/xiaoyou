package com.yoyo.xiaoyou

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.RemoteInput
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import java.util.concurrent.Executors

class XiaoyouDirectReplyReceiver : BroadcastReceiver() {
    companion object {
        private const val TAG = "XiaoyouDirectReply"
        private const val PREFERENCES_NAME = "xiaoyou_background_notifications"
        private const val KEY_BASE_URL = "base_url"
        private const val KEY_TOKEN = "token"
        private const val KEY_DEVICE_ID = "device_id"
        private val executor = Executors.newSingleThreadExecutor()
    }

    override fun onReceive(context: Context, intent: Intent) {
        val reply = RemoteInput.getResultsFromIntent(intent)
            ?.getCharSequence(XiaoyouNotificationService.DIRECT_REPLY_KEY)
            ?.toString()
            ?.trim()
            .orEmpty()
        if (reply.isEmpty()) {
            return
        }
        val pending = goAsync()
        val appContext = context.applicationContext
        val notificationId = intent.getIntExtra(
            XiaoyouNotificationService.EXTRA_NOTIFICATION_ID,
            0,
        )
        executor.execute {
            try {
                sendReply(appContext, reply)
                if (notificationId != 0) {
                    NotificationManagerCompat.from(appContext).cancel(notificationId)
                }
            } catch (error: Throwable) {
                Log.w(TAG, "Unable to send notification reply", error)
            } finally {
                pending.finish()
            }
        }
    }

    private fun sendReply(context: Context, text: String) {
        val preferences = context.getSharedPreferences(
            PREFERENCES_NAME,
            Context.MODE_PRIVATE,
        )
        val baseUrl = preferences.getString(KEY_BASE_URL, "")
            .orEmpty()
            .trim()
            .trimEnd('/')
        val token = preferences.getString(KEY_TOKEN, "").orEmpty().trim()
        val deviceId = preferences.getString(KEY_DEVICE_ID, "").orEmpty().trim()
        if (baseUrl.isEmpty() || token.isEmpty() || deviceId.isEmpty()) {
            throw IllegalStateException("Background connection is incomplete")
        }

        val connection = (
            URL("$baseUrl/v1/messages").openConnection() as HttpURLConnection
            ).apply {
            requestMethod = "POST"
            connectTimeout = 12_000
            readTimeout = 18_000
            doOutput = true
            useCaches = false
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Authorization", "Bearer $token")
        }
        try {
            val now = System.currentTimeMillis()
            val body = JSONObject().apply {
                put("message_id", "notification-${UUID.randomUUID()}")
                put("device_id", deviceId)
                put("client_sequence", now)
                put("created_at", now / 1000L)
                put("text", text)
            }.toString().toByteArray(Charsets.UTF_8)
            connection.setFixedLengthStreamingMode(body.size)
            connection.outputStream.use { it.write(body) }
            val status = connection.responseCode
            if (status !in 200..299) {
                val errorText = connection.errorStream
                    ?.bufferedReader(Charsets.UTF_8)
                    ?.use { it.readText() }
                    .orEmpty()
                throw IllegalStateException("HTTP $status $errorText")
            }
            connection.inputStream.close()
        } finally {
            connection.disconnect()
        }
    }
}

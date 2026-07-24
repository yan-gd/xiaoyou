package com.yoyo.xiaoyou

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.Executors
import kotlin.math.max

class XiaoyouNotificationService : Service() {
    companion object {
        private const val TAG = "XiaoyouNotify"
        private const val ACTION_CONFIGURE = "com.yoyo.xiaoyou.notification.CONFIGURE"
        private const val EXTRA_BASE_URL = "base_url"
        private const val EXTRA_TOKEN = "token"
        private const val EXTRA_DEVICE_ID = "device_id"
        private const val EXTRA_SEQUENCE = "sequence"
        private const val EXTRA_FOREGROUND = "app_foreground"
        private const val EXTRA_PREVIEW = "preview"
        private const val EXTRA_SOUND = "sound"
        private const val EXTRA_VIBRATION = "vibration"
        private const val SERVICE_CHANNEL_ID = "xiaoyou_background_delivery_v1"
        private const val SERVICE_NOTIFICATION_ID = 41001
        private const val MESSAGE_NOTIFICATION_BASE = 42000
        private const val POLL_DELAY_MS = 4_000L
        private const val ERROR_DELAY_MS = 9_000L

        fun configure(
            context: Context,
            baseUrl: String,
            token: String,
            deviceId: String,
            sequence: Long,
            appForeground: Boolean,
            preview: Boolean,
            sound: Boolean,
            vibration: Boolean,
        ) {
            val intent = Intent(context, XiaoyouNotificationService::class.java).apply {
                action = ACTION_CONFIGURE
                putExtra(EXTRA_BASE_URL, baseUrl)
                putExtra(EXTRA_TOKEN, token)
                putExtra(EXTRA_DEVICE_ID, deviceId)
                putExtra(EXTRA_SEQUENCE, sequence)
                putExtra(EXTRA_FOREGROUND, appForeground)
                putExtra(EXTRA_PREVIEW, preview)
                putExtra(EXTRA_SOUND, sound)
                putExtra(EXTRA_VIBRATION, vibration)
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, XiaoyouNotificationService::class.java))
        }
    }

    private val executor = Executors.newSingleThreadExecutor()
    private val cursorPreferences by lazy {
        getSharedPreferences("xiaoyou_background_notifications", Context.MODE_PRIVATE)
    }

    @Volatile
    private var running = false

    @Volatile
    private var appForeground = true

    @Volatile
    private var baseUrl = ""

    @Volatile
    private var token = ""

    @Volatile
    private var deviceId = ""

    @Volatile
    private var sequence = 0L

    @Volatile
    private var showPreview = true

    @Volatile
    private var playSound = true

    @Volatile
    private var vibrate = true

    override fun onCreate() {
        super.onCreate()
        createServiceChannel()
        startForeground(SERVICE_NOTIFICATION_ID, buildServiceNotification())
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CONFIGURE -> configureFrom(intent)
        }
        if (!running) {
            running = true
            executor.execute(::pollLoop)
        }
        return START_REDELIVER_INTENT
    }

    override fun onDestroy() {
        running = false
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun configureFrom(intent: Intent) {
        val nextDeviceId = intent.getStringExtra(EXTRA_DEVICE_ID).orEmpty().trim()
        baseUrl = intent.getStringExtra(EXTRA_BASE_URL).orEmpty().trim().trimEnd('/')
        token = intent.getStringExtra(EXTRA_TOKEN).orEmpty().trim()
        deviceId = nextDeviceId
        appForeground = intent.getBooleanExtra(EXTRA_FOREGROUND, true)
        showPreview = intent.getBooleanExtra(EXTRA_PREVIEW, true)
        playSound = intent.getBooleanExtra(EXTRA_SOUND, true)
        vibrate = intent.getBooleanExtra(EXTRA_VIBRATION, true)
        val restored = if (nextDeviceId.isEmpty()) {
            0L
        } else {
            cursorPreferences.getLong(cursorKey(nextDeviceId), 0L)
        }
        sequence = max(
            max(sequence, restored),
            intent.getLongExtra(EXTRA_SEQUENCE, 0L),
        )
        persistCursor()
    }

    private fun pollLoop() {
        while (running) {
            if (
                appForeground ||
                baseUrl.isEmpty() ||
                token.isEmpty() ||
                deviceId.isEmpty()
            ) {
                sleep(1_000L)
                continue
            }
            try {
                pollOnce()
                sleep(POLL_DELAY_MS)
            } catch (error: Throwable) {
                if (running) {
                    Log.w(TAG, "Background notification poll failed", error)
                    sleep(ERROR_DELAY_MS)
                }
            }
        }
    }

    private fun pollOnce() {
        val encodedDevice = URLEncoder.encode(deviceId, Charsets.UTF_8.name())
        val url = URL(
            "$baseUrl/v1/events?device_id=$encodedDevice&after=$sequence&limit=100",
        )
        val connection = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 12_000
            readTimeout = 22_000
            useCaches = false
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Authorization", "Bearer $token")
        }
        try {
            val status = connection.responseCode
            if (status !in 200..299) {
                connection.errorStream?.close()
                throw IllegalStateException("HTTP $status while polling notifications")
            }
            val payload = connection.inputStream.bufferedReader(Charsets.UTF_8).use {
                it.readText()
            }
            val events = JSONObject(payload).optJSONArray("events") ?: return
            var newestSequence = sequence
            for (index in 0 until events.length()) {
                val event = events.optJSONObject(index) ?: continue
                val eventSequence = event.optLong("sequence", 0L)
                if (eventSequence <= sequence) {
                    continue
                }
                newestSequence = max(newestSequence, eventSequence)
                if (event.optString("role", "assistant") == "assistant") {
                    showMessageNotification(event, eventSequence)
                }
            }
            if (newestSequence > sequence) {
                sequence = newestSequence
                persistCursor()
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun showMessageNotification(event: JSONObject, eventSequence: Long) {
        val channelId = messageChannelId(playSound, vibrate)
        createMessageChannel(channelId, playSound, vibrate)
        val messageId = event.optString("id", event.optString("event_id", "$eventSequence"))
        val kind = event.optString("kind", "text")
        val text = event.optString("text", "").trim()
        val body = if (!showPreview) {
            "小悠发来了一条新消息"
        } else {
            when (kind) {
                "image" -> "小悠发来了一张图片"
                "sticker" -> "小悠发来了一个表情包"
                "voice" -> if (text.isEmpty()) "小悠发来了一条语音" else "🎙 $text"
                else -> text.ifEmpty { "小悠发来了一条新消息" }
            }
        }
        val openApp = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(this, channelId)
            .setSmallIcon(R.drawable.ic_stat_xiaoyou)
            .setContentTitle("小悠")
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(openApp)
            .setGroup("xiaoyou_conversation")
            .setSound(if (playSound) RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION) else null)
            .setVibrate(if (vibrate) longArrayOf(0L, 180L, 90L, 180L) else longArrayOf(0L))
            .build()
        try {
            NotificationManagerCompat.from(this).notify(
                MESSAGE_NOTIFICATION_BASE + (messageId.hashCode() and 0x0fffffff),
                notification,
            )
        } catch (error: SecurityException) {
            Log.w(TAG, "Notification permission was revoked", error)
        }
    }

    private fun createServiceChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }
        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            SERVICE_CHANNEL_ID,
            "小悠后台提醒",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "保持小悠 App 的后台消息连接"
            setSound(null, null)
            enableVibration(false)
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    private fun createMessageChannel(
        channelId: String,
        sound: Boolean,
        vibration: Boolean,
    ) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }
        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            channelId,
            "小悠的消息",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "小悠发来的聊天消息和主动关心"
            enableVibration(vibration)
            vibrationPattern = if (vibration) {
                longArrayOf(0L, 180L, 90L, 180L)
            } else {
                longArrayOf(0L)
            }
            if (sound) {
                val attributes = AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION)
                    .build()
                setSound(
                    RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
                    attributes,
                )
            } else {
                setSound(null, null)
            }
        }
        manager.createNotificationChannel(channel)
    }

    private fun buildServiceNotification(): Notification {
        val openApp = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, SERVICE_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_xiaoyou)
            .setContentTitle("小悠后台提醒已开启")
            .setContentText("离开 App 后仍会及时提醒你")
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setOngoing(true)
            .setContentIntent(openApp)
            .build()
    }

    private fun messageChannelId(sound: Boolean, vibration: Boolean): String {
        return "xiaoyou_messages_v4_" +
            (if (sound) "sound" else "silent") + "_" +
            (if (vibration) "vibrate" else "still")
    }

    private fun cursorKey(id: String) = "cursor_$id"

    private fun persistCursor() {
        val id = deviceId
        if (id.isNotEmpty()) {
            cursorPreferences.edit().putLong(cursorKey(id), sequence).apply()
        }
    }

    private fun sleep(durationMs: Long) {
        try {
            Thread.sleep(durationMs)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }
}

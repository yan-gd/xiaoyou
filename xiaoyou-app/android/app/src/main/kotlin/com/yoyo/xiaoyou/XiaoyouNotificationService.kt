package com.yoyo.xiaoyou

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.app.AlarmManager
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.Person
import androidx.core.app.RemoteInput
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.content.ContextCompat
import androidx.core.graphics.drawable.IconCompat
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
        internal const val ACTION_RESTORE = "com.yoyo.xiaoyou.notification.RESTORE"
        internal const val ACTION_RESTART = "com.yoyo.xiaoyou.notification.RESTART"
        private const val EXTRA_BASE_URL = "base_url"
        private const val EXTRA_TOKEN = "token"
        private const val EXTRA_DEVICE_ID = "device_id"
        private const val EXTRA_SEQUENCE = "sequence"
        private const val EXTRA_FOREGROUND = "app_foreground"
        private const val EXTRA_PREVIEW = "preview"
        private const val EXTRA_SOUND = "sound"
        private const val EXTRA_VIBRATION = "vibration"
        private const val EXTRA_SYSTEM_PUSH = "system_push"
        private const val SERVICE_CHANNEL_ID = "xiaoyou_background_delivery_v1"
        private const val SERVICE_NOTIFICATION_ID = 41001
        private const val MESSAGE_NOTIFICATION_BASE = 42000
        private const val RESTART_REQUEST_CODE = 41002
        internal const val CONVERSATION_SHORTCUT_ID = "xiaoyou_conversation"
        internal const val DIRECT_REPLY_KEY = "xiaoyou_direct_reply"
        internal const val EXTRA_NOTIFICATION_ID = "notification_id"
        private const val PREFERENCES_NAME = "xiaoyou_background_notifications"
        private const val KEY_ENABLED = "enabled"
        private const val KEY_BASE_URL = "base_url"
        private const val KEY_TOKEN = "token"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_FOREGROUND = "app_foreground"
        private const val KEY_PREVIEW = "preview"
        private const val KEY_SOUND = "sound"
        private const val KEY_VIBRATION = "vibration"
        private const val KEY_SYSTEM_PUSH = "system_push"
        private const val POLL_DELAY_MS = 350L
        private const val ERROR_DELAY_MS = 3_000L
        private val registrationExecutor = Executors.newSingleThreadExecutor()

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
            systemPush: Boolean,
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
                putExtra(EXTRA_SYSTEM_PUSH, systemPush)
            }
            persistConfiguration(
                context = context,
                baseUrl = baseUrl,
                token = token,
                deviceId = deviceId,
                appForeground = appForeground,
                preview = preview,
                sound = sound,
                vibration = vibration,
                systemPush = systemPush,
                enabled = true,
            )
            ContextCompat.startForegroundService(context, intent)
            if (systemPush) {
                if (XiaoyouSystemPush.isActive(context)) {
                    uploadSystemPushRegistration(context, enabled = true)
                    return
                }
                XiaoyouSystemPush.enable(context) { status ->
                    if (status["active"] == true) {
                        uploadSystemPushRegistration(context, enabled = true)
                    }
                }
            }
        }

        fun stop(context: Context) {
            val preferences = context.getSharedPreferences(
                PREFERENCES_NAME,
                Context.MODE_PRIVATE,
            )
            uploadSystemPushRegistration(context, enabled = false)
            preferences.edit()
                .putBoolean(KEY_ENABLED, false)
                .putBoolean(KEY_SYSTEM_PUSH, false)
                .apply()
            XiaoyouSystemPush.disable(context) {}
            context.stopService(Intent(context, XiaoyouNotificationService::class.java))
        }

        fun systemPushStatus(context: Context): Map<String, Any> =
            XiaoyouSystemPush.status(context)

        fun enableSystemPush(
            context: Context,
            callback: (Map<String, Any>) -> Unit,
        ) {
            context.getSharedPreferences(
                PREFERENCES_NAME,
                Context.MODE_PRIVATE,
            ).edit().putBoolean(KEY_SYSTEM_PUSH, true).apply()
            XiaoyouSystemPush.enable(context) { status ->
                if (status["active"] == true) {
                    uploadSystemPushRegistration(context, enabled = true)
                }
                startFallbackService(context, forceBackground = false)
                callback(status)
            }
        }

        fun disableSystemPush(
            context: Context,
            callback: (Map<String, Any>) -> Unit,
        ) {
            uploadSystemPushRegistration(context, enabled = false)
            context.getSharedPreferences(
                PREFERENCES_NAME,
                Context.MODE_PRIVATE,
            ).edit().putBoolean(KEY_SYSTEM_PUSH, false).apply()
            XiaoyouSystemPush.disable(context) { status ->
                startFallbackService(context, forceBackground = false)
                callback(status)
            }
        }

        fun onSystemPushTokenChanged(context: Context, regId: String) {
            if (regId.isBlank() || !XiaoyouSystemPush.consented(context)) {
                return
            }
            uploadSystemPushRegistration(context, enabled = true)
            startFallbackService(context, forceBackground = false)
        }

        internal fun startIfEnabled(context: Context) {
            val preferences = context.getSharedPreferences(
                PREFERENCES_NAME,
                Context.MODE_PRIVATE,
            )
            if (!preferences.getBoolean(KEY_ENABLED, false)) {
                return
            }
            val baseUrl = preferences.getString(KEY_BASE_URL, "").orEmpty()
            val token = preferences.getString(KEY_TOKEN, "").orEmpty()
            val deviceId = preferences.getString(KEY_DEVICE_ID, "").orEmpty()
            if (baseUrl.isBlank() || token.isBlank() || deviceId.isBlank()) {
                Log.w(TAG, "Cannot restore background service without connection config")
                return
            }
            startFallbackService(context, forceBackground = true)
            if (preferences.getBoolean(KEY_SYSTEM_PUSH, false)) {
                if (XiaoyouSystemPush.isActive(context)) {
                    uploadSystemPushRegistration(context, enabled = true)
                    return
                }
                XiaoyouSystemPush.enable(context) { status ->
                    if (status["active"] == true) {
                        uploadSystemPushRegistration(context, enabled = true)
                    }
                }
            }
        }

        private fun startFallbackService(
            context: Context,
            forceBackground: Boolean,
        ) {
            try {
                ContextCompat.startForegroundService(
                    context,
                    Intent(context, XiaoyouNotificationService::class.java).apply {
                        if (forceBackground) {
                            action = ACTION_RESTORE
                        }
                    },
                )
            } catch (error: Throwable) {
                Log.w(TAG, "Unable to restore background notification service", error)
            }
        }

        private fun persistConfiguration(
            context: Context,
            baseUrl: String,
            token: String,
            deviceId: String,
            appForeground: Boolean,
            preview: Boolean,
            sound: Boolean,
            vibration: Boolean,
            systemPush: Boolean,
            enabled: Boolean,
        ) {
            context.getSharedPreferences(
                PREFERENCES_NAME,
                Context.MODE_PRIVATE,
            ).edit()
                .putBoolean(KEY_ENABLED, enabled)
                .putString(KEY_BASE_URL, baseUrl.trim().trimEnd('/'))
                .putString(KEY_TOKEN, token.trim())
                .putString(KEY_DEVICE_ID, deviceId.trim())
                .putBoolean(KEY_FOREGROUND, appForeground)
                .putBoolean(KEY_PREVIEW, preview)
                .putBoolean(KEY_SOUND, sound)
                .putBoolean(KEY_VIBRATION, vibration)
                .putBoolean(KEY_SYSTEM_PUSH, systemPush)
                .apply()
        }

        private fun uploadSystemPushRegistration(
            context: Context,
            enabled: Boolean,
        ) {
            val preferences = context.getSharedPreferences(
                PREFERENCES_NAME,
                Context.MODE_PRIVATE,
            )
            val baseUrl = preferences.getString(KEY_BASE_URL, "")
                .orEmpty()
                .trim()
                .trimEnd('/')
            val bearer = preferences.getString(KEY_TOKEN, "").orEmpty().trim()
            val deviceId = preferences.getString(KEY_DEVICE_ID, "").orEmpty().trim()
            val regId = XiaoyouSystemPush.token(context)
            if (
                baseUrl.isEmpty() ||
                bearer.isEmpty() ||
                deviceId.isEmpty() ||
                (enabled && regId.isEmpty())
            ) {
                return
            }
            val preview = preferences.getBoolean(KEY_PREVIEW, true)
            val sound = preferences.getBoolean(KEY_SOUND, true)
            val vibration = preferences.getBoolean(KEY_VIBRATION, true)
            registrationExecutor.execute {
                val connection = (
                    URL("$baseUrl/v1/devices").openConnection()
                        as HttpURLConnection
                    ).apply {
                    requestMethod = "POST"
                    connectTimeout = 8_000
                    readTimeout = 8_000
                    doOutput = true
                    useCaches = false
                    setRequestProperty("Accept", "application/json")
                    setRequestProperty("Content-Type", "application/json")
                    setRequestProperty("Authorization", "Bearer $bearer")
                }
                try {
                    val body = JSONObject().apply {
                        put("device_id", deviceId)
                        put("platform", "android")
                        put("push_provider", "vivo")
                        put("push_token", if (enabled) regId else "")
                        put("push_enabled", enabled)
                        put("push_preview", preview)
                        put("push_sound", sound)
                        put("push_vibration", vibration)
                    }.toString().toByteArray(Charsets.UTF_8)
                    connection.setFixedLengthStreamingMode(body.size)
                    connection.outputStream.use { it.write(body) }
                    val status = connection.responseCode
                    if (status !in 200..299) {
                        connection.errorStream?.close()
                        Log.w(TAG, "Push registration upload failed HTTP $status")
                    } else {
                        connection.inputStream.close()
                    }
                } catch (error: Throwable) {
                    Log.w(TAG, "Push registration upload failed", error)
                } finally {
                    connection.disconnect()
                }
            }
        }
    }

    private val executor = Executors.newSingleThreadExecutor()
    private val cursorPreferences by lazy {
        getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
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
            ACTION_RESTORE -> restoreConfiguration(forceBackground = true)
            else -> restoreConfiguration(forceBackground = false)
        }
        if (!running) {
            running = true
            executor.execute(::pollLoop)
        }
        // All connection details are persisted locally, so a null restart intent
        // can still recover after process eviction.
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // A task swipe can remove the Flutter activity without delivering the
        // paused callback reliably. The foreground service must then become a
        // real background poller instead of waiting forever in its foreground
        // state. Persist this transition so a sticky restart keeps polling.
        appForeground = false
        cursorPreferences.edit().putBoolean(KEY_FOREGROUND, false).apply()
        scheduleRestart()
        super.onTaskRemoved(rootIntent)
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
        val systemPush = intent.getBooleanExtra(EXTRA_SYSTEM_PUSH, false)
        persistConfiguration(
            context = this,
            baseUrl = baseUrl,
            token = token,
            deviceId = deviceId,
            appForeground = appForeground,
            preview = showPreview,
            sound = playSound,
            vibration = vibrate,
            systemPush = systemPush,
            enabled = true,
        )
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

    private fun restoreConfiguration(forceBackground: Boolean) {
        val preferences = cursorPreferences
        if (!preferences.getBoolean(KEY_ENABLED, false)) {
            return
        }
        baseUrl = preferences.getString(KEY_BASE_URL, "").orEmpty().trim().trimEnd('/')
        token = preferences.getString(KEY_TOKEN, "").orEmpty().trim()
        deviceId = preferences.getString(KEY_DEVICE_ID, "").orEmpty().trim()
        appForeground = if (forceBackground) {
            false
        } else {
            preferences.getBoolean(KEY_FOREGROUND, false)
        }
        showPreview = preferences.getBoolean(KEY_PREVIEW, true)
        playSound = preferences.getBoolean(KEY_SOUND, true)
        vibrate = preferences.getBoolean(KEY_VIBRATION, true)
        if (deviceId.isNotEmpty()) {
            sequence = max(
                sequence,
                preferences.getLong(cursorKey(deviceId), 0L),
            )
        }
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
            "$baseUrl/v1/events?device_id=$encodedDevice&after=$sequence&limit=100&wait=25",
        )
        val connection = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 12_000
            readTimeout = 35_000
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
            val pushDelivery = JSONObject(payload).optJSONObject("push_delivery")
                ?: JSONObject()
            val systemPushRequested =
                cursorPreferences.getBoolean(KEY_SYSTEM_PUSH, false) &&
                    XiaoyouSystemPush.isActive(this)
            var newestSequence = sequence
            for (index in 0 until events.length()) {
                val event = events.optJSONObject(index) ?: continue
                val eventSequence = event.optLong("sequence", 0L)
                if (eventSequence <= sequence) {
                    continue
                }
                val actionId = event.optString("action_id", "")
                val pushState = pushDelivery
                    .optJSONObject(actionId)
                    ?.optString("state", "")
                    .orEmpty()
                if (systemPushRequested && pushState == "pending") {
                    // Keep the cursor before this action until the server knows
                    // whether vivo accepted it. The next long-poll retry then
                    // chooses exactly one notification path.
                    break
                }
                newestSequence = max(newestSequence, eventSequence)
                val acceptedBySystemPush =
                    systemPushRequested && pushState == "accepted"
                if (
                    event.optString("role", "assistant") == "assistant" &&
                    !acceptedBySystemPush
                ) {
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

    private fun scheduleRestart() {
        val restartIntent = Intent(this, XiaoyouNotificationReceiver::class.java).apply {
            action = ACTION_RESTART
        }
        val pendingIntent = PendingIntent.getBroadcast(
            this,
            RESTART_REQUEST_CODE,
            restartIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val alarmManager = getSystemService(AlarmManager::class.java) ?: return
        alarmManager.setAndAllowWhileIdle(
            AlarmManager.ELAPSED_REALTIME_WAKEUP,
            SystemClock.elapsedRealtime() + 2_000L,
            pendingIntent,
        )
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
        val xiaoyou = Person.Builder()
            .setName("小悠")
            .setKey("xiaoyou")
            .setImportant(true)
            .setIcon(IconCompat.createWithResource(this, R.mipmap.ic_launcher))
            .build()
        publishConversationShortcut(xiaoyou)
        val notificationId =
            MESSAGE_NOTIFICATION_BASE + (messageId.hashCode() and 0x0fffffff)
        val replyIntent = Intent(this, XiaoyouDirectReplyReceiver::class.java).apply {
            putExtra(EXTRA_NOTIFICATION_ID, notificationId)
        }
        val mutableFlag = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            PendingIntent.FLAG_MUTABLE
        } else {
            0
        }
        val replyPendingIntent = PendingIntent.getBroadcast(
            this,
            notificationId,
            replyIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or mutableFlag,
        )
        val replyAction = NotificationCompat.Action.Builder(
            R.drawable.ic_stat_xiaoyou,
            "直接回复",
            replyPendingIntent,
        )
            .addRemoteInput(
                RemoteInput.Builder(DIRECT_REPLY_KEY)
                    .setLabel("回复小悠")
                    .build(),
            )
            .setAllowGeneratedReplies(true)
            .build()
        val timestamp = event.optLong(
            "created_at",
            System.currentTimeMillis() / 1000L,
        ) * 1000L
        val notification = NotificationCompat.Builder(this, channelId)
            .setSmallIcon(R.drawable.ic_stat_xiaoyou)
            .setContentTitle("小悠")
            .setContentText(body)
            .setStyle(
                NotificationCompat.MessagingStyle(xiaoyou)
                    .setConversationTitle("小悠")
                    .addMessage(body, timestamp, xiaoyou),
            )
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(openApp)
            .setShortcutId(CONVERSATION_SHORTCUT_ID)
            .addPerson(xiaoyou)
            .addAction(replyAction)
            .setNumber(1)
            .setGroup("xiaoyou_conversation")
            .setSound(if (playSound) RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION) else null)
            .setVibrate(if (vibrate) longArrayOf(0L, 180L, 90L, 180L) else longArrayOf(0L))
            .build()
        try {
            NotificationManagerCompat.from(this).notify(
                notificationId,
                notification,
            )
        } catch (error: SecurityException) {
            Log.w(TAG, "Notification permission was revoked", error)
        }
    }

    private fun publishConversationShortcut(person: Person) {
        val intent = Intent(this, MainActivity::class.java).apply {
            action = Intent.ACTION_VIEW
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val shortcut = ShortcutInfoCompat.Builder(
            this,
            CONVERSATION_SHORTCUT_ID,
        )
            .setShortLabel("小悠")
            .setLongLabel("和小悠聊天")
            .setIcon(IconCompat.createWithResource(this, R.mipmap.ic_launcher))
            .setIntent(intent)
            .setPerson(person)
            .setLongLived(true)
            .build()
        ShortcutManagerCompat.pushDynamicShortcut(this, shortcut)
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
            setShowBadge(true)
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

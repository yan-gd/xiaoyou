package com.yoyo.xiaoyou

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * 小悠通知注册与偏好持久化（vivo 系统推送模式）。
 *
 * 不再运行前台轮询服务：消息提醒完全由 vivo 系统通道负责，
 * 本对象只负责把连接配置与推送偏好持久化，并向服务器上报 regId。
 */
object XiaoyouNotificationService {
    private const val TAG = "XiaoyouNotify"
    private const val PREFERENCES_NAME = "xiaoyou_background_notifications"
    private const val KEY_ENABLED = "enabled"
    private const val KEY_BASE_URL = "base_url"
    private const val KEY_TOKEN = "token"
    private const val KEY_DEVICE_ID = "device_id"
    private const val KEY_PREVIEW = "preview"
    private const val KEY_SOUND = "sound"
    private const val KEY_VIBRATION = "vibration"
    private const val KEY_SYSTEM_PUSH = "system_push"
    private val registrationExecutor = Executors.newSingleThreadExecutor()

    fun configure(
        context: Context,
        baseUrl: String,
        token: String,
        deviceId: String,
        preview: Boolean,
        sound: Boolean,
        vibration: Boolean,
        systemPush: Boolean,
    ) {
        persistConfiguration(
            context = context,
            baseUrl = baseUrl,
            token = token,
            deviceId = deviceId,
            preview = preview,
            sound = sound,
            vibration = vibration,
            systemPush = systemPush,
            enabled = true,
        )
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
            callback(status)
        }
    }

    fun onSystemPushTokenChanged(context: Context, regId: String) {
        if (regId.isBlank() || !XiaoyouSystemPush.consented(context)) {
            return
        }
        uploadSystemPushRegistration(context, enabled = true)
    }

    fun persistConfiguration(
        context: Context,
        baseUrl: String,
        token: String,
        deviceId: String,
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
            .putBoolean(KEY_PREVIEW, preview)
            .putBoolean(KEY_SOUND, sound)
            .putBoolean(KEY_VIBRATION, vibration)
            .putBoolean(KEY_SYSTEM_PUSH, systemPush)
            .apply()
    }

    fun uploadSystemPushRegistration(
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

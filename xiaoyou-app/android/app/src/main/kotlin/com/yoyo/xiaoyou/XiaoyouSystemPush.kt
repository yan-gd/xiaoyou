package com.yoyo.xiaoyou

import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.vivo.push.IPushActionListener
import com.vivo.push.PushClient
import com.vivo.push.PushConfig
import com.vivo.push.listener.IPushQueryActionListener

/**
 * Owns the vivo system-push registration lifecycle.
 *
 * Initialization happens only after the explicit privacy consent stored by
 * Flutter. A failure never disables the existing foreground polling fallback.
 */
object XiaoyouSystemPush {
    private const val TAG = "XiaoyouSystemPush"
    private const val PREFERENCES = "xiaoyou_system_push"
    private const val KEY_CONSENT = "consent"
    private const val KEY_ACTIVE = "active"
    private const val KEY_REG_ID = "reg_id"
    private const val META_APP_ID = "com.vivo.push.app_id"
    private const val META_APP_KEY = "com.vivo.push.api_key"

    @Volatile
    private var registrationInFlight = false

    /** 注册成功后，SDK 的 getRegId 可能因“两秒内重复调用”限制暂时返回空。
     *  此时不报错，等待 vivo 的 onSystemPushTokenChanged 回调带回 regId。 */
    private const val REGISTRATION_PENDING_REFRESH_MS = 2_000L

    /** turnOnPush 已提交但 regId 尚未到达的等待窗口。 */
    private const val REGISTRATION_ACCEPT_WINDOW_MS = 8_000L

    fun status(context: Context): Map<String, Any> {
        val preferences = context.getSharedPreferences(
            PREFERENCES,
            Context.MODE_PRIVATE,
        )
        val configured = credentialsConfigured(context)
        val consented = preferences.getBoolean(KEY_CONSENT, false)
        val supported = if (configured && consented) {
            try {
                initialize(context).isSupport
            } catch (_: Throwable) {
                false
            }
        } else {
            configured
        }
        val token = preferences.getString(KEY_REG_ID, "").orEmpty()
        return mapOf(
            "provider" to "vivo",
            "configured" to configured,
            "supported" to supported,
            "consented" to consented,
            "active" to (
                configured &&
                    supported &&
                    consented &&
                    token.isNotBlank()
                ),
        )
    }

    fun isActive(context: Context): Boolean =
        status(context)["active"] == true

    fun consented(context: Context): Boolean =
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .getBoolean(KEY_CONSENT, false)

    fun token(context: Context): String =
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .getString(KEY_REG_ID, "")
            .orEmpty()
            .trim()

    fun enable(
        context: Context,
        callback: (Map<String, Any>) -> Unit,
    ) {
        val appContext = context.applicationContext
        val preferences = appContext.getSharedPreferences(
            PREFERENCES,
            Context.MODE_PRIVATE,
        )
        preferences.edit().putBoolean(KEY_CONSENT, true).apply()
        if (!credentialsConfigured(appContext)) {
            callback(statusWithError(appContext, "not_configured"))
            return
        }
        val client = try {
            initialize(appContext)
        } catch (error: Throwable) {
            Log.w(TAG, "vivo push initialization failed", error)
            callback(statusWithError(appContext, "initialization_failed"))
            return
        }
        if (!client.isSupport) {
            callback(statusWithError(appContext, "unsupported_device"))
            return
        }
        val existingToken = token(appContext)
        if (existingToken.isNotEmpty()) {
            callback(status(appContext))
            return
        }
        synchronized(this) {
            if (registrationInFlight) {
                callback(statusWithError(appContext, "registration_pending"))
                return
            }
            registrationInFlight = true
        }
        queryRegistration(client) { queriedToken ->
            if (queriedToken.isNotEmpty()) {
                registrationInFlight = false
                saveRegistration(appContext, queriedToken)
                callback(status(appContext))
                return@queryRegistration
            }
            client.turnOnPush(
                IPushActionListener { state ->
                    if (state != 0) {
                        registrationInFlight = false
                        callback(
                            statusWithError(
                                appContext,
                                "registration_failed_$state",
                            ),
                        )
                        return@IPushActionListener
                    }
                    queryRegistration(client) { registeredToken ->
                        if (registeredToken.isNotEmpty()) {
                            registrationInFlight = false
                            saveRegistration(appContext, registeredToken)
                            callback(status(appContext))
                            return@queryRegistration
                        }
                        // vivo SDK 刚注册成功，但 getRegId 受“两秒内重复调用”
                        // 限制可能返回空。此时不报错：等待 SDK 的
                        // onSystemPushTokenChanged 回调（会保存 regId 并置 active），
                        // 首次注册时 regId 由异步回调带回，最多等待
                        // REGISTRATION_ACCEPT_WINDOW_MS 让回调落地。
                        val poll = object : Runnable {
                            private var attempts = 0
                            override fun run() {
                                val tokenNow = token(appContext)
                                if (tokenNow.isNotEmpty()) {
                                    registrationInFlight = false
                                    callback(status(appContext))
                                    return
                                }
                                attempts++
                                if (attempts * REGISTRATION_PENDING_REFRESH_MS >=
                                    REGISTRATION_ACCEPT_WINDOW_MS
                                ) {
                                    registrationInFlight = false
                                    callback(
                                        statusWithError(
                                            appContext,
                                            "missing_registration_id",
                                        ),
                                    )
                                    return
                                }
                                Handler(Looper.getMainLooper())
                                    .postDelayed(this, REGISTRATION_PENDING_REFRESH_MS)
                            }
                        }
                        Handler(Looper.getMainLooper())
                            .postDelayed(poll, REGISTRATION_PENDING_REFRESH_MS)
                    }
                },
            )
        }
    }

    fun disable(
        context: Context,
        callback: (Map<String, Any>) -> Unit,
    ) {
        val appContext = context.applicationContext
        val preferences = appContext.getSharedPreferences(
            PREFERENCES,
            Context.MODE_PRIVATE,
        )
        preferences.edit()
            .putBoolean(KEY_CONSENT, false)
            .putBoolean(KEY_ACTIVE, false)
            .apply()
        if (!credentialsConfigured(appContext)) {
            preferences.edit().remove(KEY_REG_ID).apply()
            callback(status(appContext))
            return
        }
        try {
            initialize(appContext).turnOffPush(
                IPushActionListener {
                    preferences.edit().remove(KEY_REG_ID).apply()
                    callback(status(appContext))
                },
            )
        } catch (error: Throwable) {
            Log.w(TAG, "Unable to turn off vivo push", error)
            preferences.edit().remove(KEY_REG_ID).apply()
            callback(status(appContext))
        }
    }

    fun saveRegistration(context: Context, regId: String) {
        val normalized = regId.trim()
        if (normalized.isEmpty()) {
            return
        }
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_REG_ID, normalized)
            .putBoolean(KEY_ACTIVE, true)
            .apply()
        XiaoyouNotificationService.onSystemPushTokenChanged(context, normalized)
    }

    private fun initialize(context: Context): PushClient {
        val client = PushClient.getInstance(context.applicationContext)
        client.initialize(
            PushConfig.Builder()
                .agreePrivacyStatement(true)
                .build(),
        )
        return client
    }

    private fun queryRegistration(
        client: PushClient,
        callback: (String) -> Unit,
    ) {
        client.getRegId(
            object : IPushQueryActionListener {
                override fun onSuccess(value: String?) {
                    callback(value.orEmpty().trim())
                }

                override fun onFail(error: Int?) {
                    callback("")
                }
            },
        )
    }

    private fun credentialsConfigured(context: Context): Boolean {
        return try {
            val applicationInfo = context.packageManager.getApplicationInfo(
                context.packageName,
                PackageManager.GET_META_DATA,
            )
            val appId = applicationInfo.metaData
                ?.get(META_APP_ID)
                ?.toString()
                .orEmpty()
                .trim()
            val appKey = applicationInfo.metaData
                ?.getString(META_APP_KEY)
                .orEmpty()
                .trim()
            appId.isNotEmpty() &&
                appId != "0" &&
                appKey.isNotEmpty() &&
                appKey != "disabled"
        } catch (_: Throwable) {
            false
        }
    }

    private fun statusWithError(
        context: Context,
        error: String,
    ): Map<String, Any> = status(context) + mapOf("error" to error)
}

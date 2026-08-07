package com.yoyo.xiaoyou

import android.Manifest
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.MediaScannerConnection
import android.os.Build
import android.os.Environment
import android.os.PowerManager
import android.provider.MediaStore
import android.provider.Settings
import android.net.Uri
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.util.concurrent.Executors
import kotlin.math.roundToInt

class MainActivity : FlutterFragmentActivity() {
    private data class PendingImageSave(
        val bytes: ByteArray,
        val fileName: String,
        val mimeType: String,
        val result: MethodChannel.Result,
    )

    private var pendingNotificationResult: MethodChannel.Result? = null
    private var pendingImageSave: PendingImageSave? = null
    private val realtimeAudioExecutor = Executors.newSingleThreadExecutor()
    private val realtimeAudioLock = Any()
    private var realtimeAudioTrack: AudioTrack? = null
    private var realtimeAudioSampleRate = 24000
    private var realtimeAudioGain = 2.0f

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            pendingNotificationResult?.success(notificationsEnabled())
            pendingNotificationResult = null
        }

    private val storagePermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            val pending = pendingImageSave
            pendingImageSave = null
            if (pending == null) {
                return@registerForActivityResult
            }
            if (!granted) {
                pending.result.error(
                    "photo_permission_denied",
                    "Photo storage permission was denied.",
                    null,
                )
                return@registerForActivityResult
            }
            saveImageNow(pending)
        }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            "com.yoyo.xiaoyou/system",
        ).setMethodCallHandler { call, result ->
            when (call.method) {
                "notificationsEnabled" -> result.success(notificationsEnabled())
                "batteryOptimizationIgnored" ->
                    result.success(batteryOptimizationIgnored())
                "openBatteryOptimizationSettings" -> {
                    val directIntent = Intent(
                        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:$packageName"),
                    )
                    try {
                        startActivity(directIntent)
                    } catch (_: Throwable) {
                        startActivity(
                            Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS),
                        )
                    }
                    result.success(null)
                }
                "requestNotificationPermission" -> requestNotificationPermission(result)
                "openNotificationSettings" -> {
                    val intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).apply {
                        putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                    }
                    startActivity(intent)
                    result.success(null)
                }
                "openExternalUrl" -> {
                    val rawUrl = call.argument<String>("url").orEmpty().trim()
                    val uri = runCatching { Uri.parse(rawUrl) }.getOrNull()
                    if (
                        uri == null ||
                        uri.host.isNullOrBlank() ||
                        (uri.scheme != "https" && uri.scheme != "http")
                    ) {
                        result.error(
                            "invalid_external_url",
                            "Only absolute HTTP(S) URLs are allowed.",
                            null,
                        )
                    } else {
                        try {
                            startActivity(Intent(Intent.ACTION_VIEW, uri))
                            result.success(null)
                        } catch (error: Throwable) {
                            result.error(
                                "external_url_unavailable",
                                error.message,
                                null,
                            )
                        }
                    }
                }
                "configureBackgroundNotifications" ->
                    configureBackgroundNotifications(call, result)
                "systemPushStatus" ->
                    result.success(
                        XiaoyouNotificationService.systemPushStatus(this),
                    )
                "enableSystemPush" -> {
                    XiaoyouNotificationService.enableSystemPush(this) { status ->
                        runOnUiThread { result.success(status) }
                    }
                }
                "disableSystemPush" -> {
                    XiaoyouNotificationService.disableSystemPush(this) { status ->
                        runOnUiThread { result.success(status) }
                    }
                }
                else -> result.notImplemented()
            }
        }
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            "com.yoyo.xiaoyou/media",
        ).setMethodCallHandler { call, result ->
            when (call.method) {
                "saveImageToGallery" -> saveImageToGallery(call, result)
                else -> result.notImplemented()
            }
        }
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            "com.yoyo.xiaoyou/realtime_audio",
        ).setMethodCallHandler { call, result ->
            when (call.method) {
                "start" -> {
                    try {
                        startRealtimeAudio(
                            call.argument<Number>("sampleRate")?.toInt() ?: 24000,
                            call.argument<Number>("gain")?.toFloat() ?: 2.0f,
                        )
                        result.success(null)
                    } catch (error: Throwable) {
                        result.error(
                            "realtime_audio_start_failed",
                            error.message,
                            null,
                        )
                    }
                }
                "write" -> {
                    val pcm = call.argument<ByteArray>("pcm")
                    if (pcm == null || pcm.isEmpty()) {
                        result.success(null)
                    } else {
                        realtimeAudioExecutor.execute {
                            val gain = synchronized(realtimeAudioLock) {
                                realtimeAudioGain
                            }
                            val amplified = amplifyPcm16(
                                pcm,
                                gain,
                            )
                            val track = synchronized(realtimeAudioLock) {
                                realtimeAudioTrack
                            }
                            if (track != null) {
                                try {
                                    // AudioTrack.write can block while its
                                    // native buffer drains. Never hold the
                                    // state lock here: positionMs is handled
                                    // on Android's main thread and used to
                                    // stall every Flutter frame until this
                                    // blocking write returned.
                                    track.write(
                                        amplified,
                                        0,
                                        amplified.size,
                                        AudioTrack.WRITE_BLOCKING,
                                    )
                                } catch (_: Throwable) {
                                    // A concurrent interruption may release
                                    // the old track after we took the snapshot.
                                    // The next reply will create a fresh one.
                                }
                            }
                        }
                        result.success(null)
                    }
                }
                "positionMs" -> {
                    val snapshot = synchronized(realtimeAudioLock) {
                        Pair(realtimeAudioTrack, realtimeAudioSampleRate)
                    }
                    val position = try {
                        val frames = snapshot.first
                            ?.playbackHeadPosition
                            ?.toLong()
                            ?.and(0xffffffffL)
                            ?: 0L
                        (frames * 1000L / snapshot.second).toInt()
                    } catch (_: Throwable) {
                        0
                    }
                    result.success(position)
                }
                "stop" -> {
                    stopRealtimeAudio()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }

    private fun startRealtimeAudio(sampleRate: Int, gain: Float) {
        stopRealtimeAudio()
        val safeRate = sampleRate.coerceIn(8000, 48000)
        val minimum = AudioTrack.getMinBufferSize(
            safeRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = maxOf(minimum, safeRate / 2)
        val builder = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    // Use the media stream so voice-room playback follows the
                    // phone's media volume instead of the quieter call stream.
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(safeRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(bufferSize)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            builder.setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
        }
        val track = builder.build()
        track.setVolume(1.0f)
        track.play()
        synchronized(realtimeAudioLock) {
            realtimeAudioSampleRate = safeRate
            realtimeAudioGain = gain.coerceIn(1.0f, 4.0f)
            realtimeAudioTrack = track
        }
    }

    private fun amplifyPcm16(pcm: ByteArray, gain: Float): ByteArray {
        if (gain <= 1.001f) {
            return pcm
        }
        val output = pcm.copyOf()
        var index = 0
        while (index + 1 < output.size) {
            val low = output[index].toInt() and 0xff
            val high = output[index + 1].toInt()
            val sample = ((high shl 8) or low).toShort().toInt()
            val amplified = (sample * gain)
                .roundToInt()
                .coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
            output[index] = (amplified and 0xff).toByte()
            output[index + 1] = ((amplified shr 8) and 0xff).toByte()
            index += 2
        }
        return output
    }

    private fun stopRealtimeAudio() {
        val track = synchronized(realtimeAudioLock) {
            val current = realtimeAudioTrack
            realtimeAudioTrack = null
            current
        } ?: return
        try {
            track.pause()
            track.flush()
            track.stop()
        } catch (_: Throwable) {
            // AudioTrack may already have stopped after an audio-route change.
        } finally {
            track.release()
        }
    }

    override fun onDestroy() {
        stopRealtimeAudio()
        realtimeAudioExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun notificationsEnabled(): Boolean {
        val appNotificationsEnabled =
            NotificationManagerCompat.from(this).areNotificationsEnabled()
        val runtimePermissionGranted =
            Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.POST_NOTIFICATIONS,
                ) == PackageManager.PERMISSION_GRANTED
        return appNotificationsEnabled && runtimePermissionGranted
    }

    private fun batteryOptimizationIgnored(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            return true
        }
        val powerManager = getSystemService(PowerManager::class.java)
        return powerManager?.isIgnoringBatteryOptimizations(packageName) == true
    }

    private fun requestNotificationPermission(result: MethodChannel.Result) {
        if (notificationsEnabled()) {
            result.success(true)
            return
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            result.success(false)
            return
        }
        if (pendingNotificationResult != null) {
            // 上一次请求未收到系统回调（用户未响应或系统延迟）。
            // 以当前实际权限状态应答，并清空残留，避免后续请求一直失败。
            pendingNotificationResult?.success(notificationsEnabled())
            pendingNotificationResult = null
        }
        pendingNotificationResult = result
        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun configureBackgroundNotifications(
        call: MethodCall,
        result: MethodChannel.Result,
    ) {
        val baseUrl = call.argument<String>("baseUrl").orEmpty().trim()
        val token = call.argument<String>("token").orEmpty().trim()
        val deviceId = call.argument<String>("deviceId").orEmpty().trim()
        if (baseUrl.isEmpty() || token.isEmpty() || deviceId.isEmpty()) {
            result.error(
                "invalid_notification_configuration",
                "Background notification connection is incomplete.",
                null,
            )
            return
        }
        XiaoyouNotificationService.configure(
            this,
            baseUrl,
            token,
            deviceId,
            call.argument<Boolean>("preview") ?: true,
            call.argument<Boolean>("sound") ?: true,
            call.argument<Boolean>("vibration") ?: true,
            call.argument<Boolean>("systemPush") ?: false,
        )
        result.success(true)
    }

    private fun saveImageToGallery(call: MethodCall, result: MethodChannel.Result) {
        val bytes = call.argument<ByteArray>("bytes")
        val requestedName = call.argument<String>("fileName")
        val requestedMimeType = call.argument<String>("mimeType")
        if (bytes == null || bytes.isEmpty()) {
            result.error("invalid_image", "Image data is empty.", null)
            return
        }
        val fileName = (requestedName ?: "xiaoyou_${System.currentTimeMillis()}.jpg")
            .replace(Regex("[^A-Za-z0-9._-]"), "_")
        val mimeType = requestedMimeType
            ?.takeIf { it.startsWith("image/") }
            ?: "image/jpeg"
        val pending = PendingImageSave(bytes, fileName, mimeType, result)
        if (
            Build.VERSION.SDK_INT < Build.VERSION_CODES.Q &&
            ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.WRITE_EXTERNAL_STORAGE,
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            if (pendingImageSave != null) {
                result.error(
                    "photo_save_pending",
                    "Another image save is already active.",
                    null,
                )
                return
            }
            pendingImageSave = pending
            storagePermissionLauncher.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
            return
        }
        saveImageNow(pending)
    }

    private fun saveImageNow(pending: PendingImageSave) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.Images.Media.DISPLAY_NAME, pending.fileName)
                    put(MediaStore.Images.Media.MIME_TYPE, pending.mimeType)
                    put(
                        MediaStore.Images.Media.RELATIVE_PATH,
                        "${Environment.DIRECTORY_PICTURES}/Xiaoyou",
                    )
                    put(MediaStore.Images.Media.IS_PENDING, 1)
                }
                val uri = contentResolver.insert(
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                    values,
                ) ?: throw IllegalStateException("Unable to create gallery item.")
                try {
                    contentResolver.openOutputStream(uri)?.use { stream ->
                        stream.write(pending.bytes)
                    } ?: throw IllegalStateException("Unable to open gallery output.")
                    values.clear()
                    values.put(MediaStore.Images.Media.IS_PENDING, 0)
                    contentResolver.update(uri, values, null, null)
                } catch (error: Throwable) {
                    contentResolver.delete(uri, null, null)
                    throw error
                }
            } else {
                val directory = File(
                    Environment.getExternalStoragePublicDirectory(
                        Environment.DIRECTORY_PICTURES,
                    ),
                    "Xiaoyou",
                )
                if (!directory.exists() && !directory.mkdirs()) {
                    throw IllegalStateException("Unable to create gallery directory.")
                }
                val file = File(directory, pending.fileName)
                file.writeBytes(pending.bytes)
                MediaScannerConnection.scanFile(
                    this,
                    arrayOf(file.absolutePath),
                    arrayOf(pending.mimeType),
                    null,
                )
            }
            pending.result.success(null)
        } catch (error: Throwable) {
            pending.result.error(
                "photo_save_failed",
                error.message ?: "Unable to save image.",
                null,
            )
        }
    }
}

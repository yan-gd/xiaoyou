package com.yoyo.xiaoyou

import android.Manifest
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaScannerConnection
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.provider.Settings
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.File

class MainActivity : FlutterFragmentActivity() {
    private data class PendingImageSave(
        val bytes: ByteArray,
        val fileName: String,
        val mimeType: String,
        val result: MethodChannel.Result,
    )

    private var pendingNotificationResult: MethodChannel.Result? = null
    private var pendingImageSave: PendingImageSave? = null

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
                "requestNotificationPermission" -> requestNotificationPermission(result)
                "openNotificationSettings" -> {
                    val intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).apply {
                        putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                    }
                    startActivity(intent)
                    result.success(null)
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
            result.error(
                "notification_permission_pending",
                "A notification permission request is already active.",
                null,
            )
            return
        }
        pendingNotificationResult = result
        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
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

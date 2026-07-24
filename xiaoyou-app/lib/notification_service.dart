import 'dart:io';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter/services.dart';

class AppNotificationService {
  AppNotificationService._();

  static final instance = AppNotificationService._();

  final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();
  static const _systemChannel = MethodChannel('com.yoyo.xiaoyou/system');

  bool _initialized = false;
  Future<void>? _initialization;

  Future<void> initialize() {
    if (_initialized) {
      return Future.value();
    }
    return _initialization ??= _initialize();
  }

  Future<void> _initialize() async {
    const settings = InitializationSettings(
      android: AndroidInitializationSettings('ic_stat_xiaoyou'),
      iOS: DarwinInitializationSettings(
        requestAlertPermission: false,
        requestBadgePermission: false,
        requestSoundPermission: false,
      ),
    );
    try {
      final initialized = await _plugin
          .initialize(settings)
          .timeout(const Duration(seconds: 8));
      if (initialized == false) {
        throw StateError('Local notification initialization was rejected.');
      }
      _initialized = true;
    } finally {
      if (!_initialized) {
        _initialization = null;
      }
    }
  }

  Future<bool> requestPermission() async {
    if (Platform.isAndroid) {
      final granted = await _systemChannel
          .invokeMethod<bool>('requestNotificationPermission')
          .timeout(const Duration(seconds: 20));
      if (granted != true) {
        return false;
      }
      await initialize();
      return true;
    }
    if (Platform.isIOS) {
      await initialize();
      final ios = _plugin.resolvePlatformSpecificImplementation<
          IOSFlutterLocalNotificationsPlugin>();
      return await ios
              ?.requestPermissions(
                alert: true,
                badge: true,
                sound: true,
              )
              .timeout(const Duration(seconds: 20)) ??
          false;
    }
    return true;
  }

  Future<bool> notificationsEnabled() async {
    if (Platform.isAndroid) {
      return await _systemChannel
              .invokeMethod<bool>('notificationsEnabled')
              .timeout(const Duration(seconds: 5)) ??
          false;
    }
    return false;
  }

  Future<void> openNotificationSettings() async {
    if (!Platform.isAndroid) {
      return;
    }
    await _systemChannel
        .invokeMethod<void>('openNotificationSettings')
        .timeout(const Duration(seconds: 5));
  }

  Future<void> showMessage({
    required String messageId,
    required String body,
    required bool sound,
    required bool vibration,
  }) async {
    await initialize();
    final notificationId = messageId.hashCode & 0x7fffffff;
    final channelId = 'xiaoyou_messages_'
        '${sound ? 'sound' : 'silent'}_'
        '${vibration ? 'vibrate' : 'still'}';
    final details = NotificationDetails(
      android: AndroidNotificationDetails(
        channelId,
        '小悠的消息',
        channelDescription: '小悠发来的聊天消息和主动关心',
        importance: Importance.high,
        priority: Priority.high,
        category: AndroidNotificationCategory.message,
        visibility: NotificationVisibility.private,
        playSound: sound,
        enableVibration: vibration,
        groupKey: 'xiaoyou_conversation',
      ),
      iOS: DarwinNotificationDetails(
        presentAlert: true,
        presentBadge: true,
        presentSound: sound,
      ),
    );
    await _plugin.show(
      notificationId,
      '小悠',
      body.trim().isEmpty ? '小悠发来了一条新消息' : body.trim(),
      details,
      payload: messageId,
    );
  }

  Future<void> cancelAll() async {
    if (!_initialized) {
      return;
    }
    try {
      await _plugin.cancelAll();
    } catch (_) {
      // Notification cleanup is best effort and must not affect chat startup.
    }
  }
}

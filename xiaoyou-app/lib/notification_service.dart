import 'dart:io';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';

class AppNotificationService {
  AppNotificationService._();

  static final instance = AppNotificationService._();

  final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();
  bool _initialized = false;

  Future<void> initialize() async {
    if (_initialized) {
      return;
    }
    const settings = InitializationSettings(
      android: AndroidInitializationSettings('ic_stat_xiaoyou'),
      iOS: DarwinInitializationSettings(
        requestAlertPermission: false,
        requestBadgePermission: false,
        requestSoundPermission: false,
      ),
    );
    await _plugin.initialize(settings);
    _initialized = true;
  }

  Future<bool> requestPermission() async {
    await initialize();
    if (Platform.isAndroid) {
      final android = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      return await android?.requestNotificationsPermission() ?? true;
    }
    if (Platform.isIOS) {
      final ios = _plugin.resolvePlatformSpecificImplementation<
          IOSFlutterLocalNotificationsPlugin>();
      return await ios?.requestPermissions(
            alert: true,
            badge: true,
            sound: true,
          ) ??
          false;
    }
    return true;
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
    await initialize();
    await _plugin.cancelAll();
  }
}

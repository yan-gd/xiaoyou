import 'dart:io';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter/services.dart';

class SystemPushStatus {
  const SystemPushStatus({
    this.provider = 'vivo',
    this.configured = false,
    this.supported = false,
    this.consented = false,
    this.active = false,
    this.error = '',
  });

  factory SystemPushStatus.fromMap(Map<Object?, Object?>? value) {
    final payload = value ?? const <Object?, Object?>{};
    return SystemPushStatus(
      provider: '${payload['provider'] ?? 'vivo'}',
      configured: payload['configured'] == true,
      supported: payload['supported'] == true,
      consented: payload['consented'] == true,
      active: payload['active'] == true,
      error: '${payload['error'] ?? ''}',
    );
  }

  final String provider;
  final bool configured;
  final bool supported;
  final bool consented;
  final bool active;
  final String error;
}

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

  Future<bool> batteryOptimizationIgnored() async {
    if (!Platform.isAndroid) {
      return true;
    }
    return await _systemChannel
            .invokeMethod<bool>('batteryOptimizationIgnored')
            .timeout(const Duration(seconds: 5)) ??
        false;
  }

  Future<void> openBatteryOptimizationSettings() async {
    if (!Platform.isAndroid) {
      return;
    }
    await _systemChannel
        .invokeMethod<void>('openBatteryOptimizationSettings')
        .timeout(const Duration(seconds: 5));
  }

  Future<void> openNotificationSettings() async {
    if (!Platform.isAndroid) {
      return;
    }
    await _systemChannel
        .invokeMethod<void>('openNotificationSettings')
        .timeout(const Duration(seconds: 5));
  }

  Future<void> configureBackgroundDelivery({
    required String baseUrl,
    required String token,
    required String deviceId,
    required bool preview,
    required bool sound,
    required bool vibration,
    required bool systemPush,
  }) async {
    if (!Platform.isAndroid) {
      return;
    }
    await _systemChannel.invokeMethod<bool>(
      'configureBackgroundNotifications',
      {
        'baseUrl': baseUrl,
        'token': token,
        'deviceId': deviceId,
        'preview': preview,
        'sound': sound,
        'vibration': vibration,
        'systemPush': systemPush,
      },
    ).timeout(const Duration(seconds: 8));
  }

  Future<SystemPushStatus> systemPushStatus() async {
    if (!Platform.isAndroid) {
      return const SystemPushStatus();
    }
    final value = await _systemChannel
        .invokeMethod<Map<Object?, Object?>>('systemPushStatus')
        .timeout(const Duration(seconds: 8));
    return SystemPushStatus.fromMap(value);
  }

  Future<SystemPushStatus> enableSystemPush() async {
    if (!Platform.isAndroid) {
      return const SystemPushStatus(error: 'unsupported_platform');
    }
    final value = await _systemChannel
        .invokeMethod<Map<Object?, Object?>>('enableSystemPush')
        .timeout(const Duration(seconds: 25));
    return SystemPushStatus.fromMap(value);
  }

  Future<SystemPushStatus> disableSystemPush() async {
    if (!Platform.isAndroid) {
      return const SystemPushStatus();
    }
    final value = await _systemChannel
        .invokeMethod<Map<Object?, Object?>>('disableSystemPush')
        .timeout(const Duration(seconds: 15));
    return SystemPushStatus.fromMap(value);
  }

  Future<void> showMessage({
    required String messageId,
    required String body,
    required bool sound,
    required bool vibration,
  }) async {
    await initialize();
    final notificationId = messageId.hashCode & 0x7fffffff;
    final channelId = 'xiaoyou_messages_v4_'
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

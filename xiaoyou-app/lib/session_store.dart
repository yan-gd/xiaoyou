import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

class SavedConnection {
  const SavedConnection({
    required this.baseUrl,
    required this.deviceId,
    required this.appLockEnabled,
  });

  final String baseUrl;
  final String deviceId;
  final bool appLockEnabled;

  SavedConnection copyWith({bool? appLockEnabled}) {
    return SavedConnection(
      baseUrl: baseUrl,
      deviceId: deviceId,
      appLockEnabled: appLockEnabled ?? this.appLockEnabled,
    );
  }
}

class AppPreferences {
  const AppPreferences({
    this.notificationsEnabled = false,
    this.notificationSound = true,
    this.notificationVibration = true,
    this.notificationPreview = true,
    this.notificationExplicitlyDisabled = false,
    this.fontScale = 1,
    this.compactMessages = false,
    this.palette = 'rose',
    this.bubbleRadius = 19,
  });

  final bool notificationsEnabled;
  final bool notificationSound;
  final bool notificationVibration;
  final bool notificationPreview;
  final bool notificationExplicitlyDisabled;
  final double fontScale;
  final bool compactMessages;
  final String palette;
  final double bubbleRadius;

  AppPreferences copyWith({
    bool? notificationsEnabled,
    bool? notificationSound,
    bool? notificationVibration,
    bool? notificationPreview,
    bool? notificationExplicitlyDisabled,
    double? fontScale,
    bool? compactMessages,
    String? palette,
    double? bubbleRadius,
  }) {
    return AppPreferences(
      notificationsEnabled: notificationsEnabled ?? this.notificationsEnabled,
      notificationSound: notificationSound ?? this.notificationSound,
      notificationVibration:
          notificationVibration ?? this.notificationVibration,
      notificationPreview: notificationPreview ?? this.notificationPreview,
      notificationExplicitlyDisabled:
          notificationExplicitlyDisabled ?? this.notificationExplicitlyDisabled,
      fontScale: fontScale ?? this.fontScale,
      compactMessages: compactMessages ?? this.compactMessages,
      palette: palette ?? this.palette,
      bubbleRadius: bubbleRadius ?? this.bubbleRadius,
    );
  }
}

class SessionStore {
  SessionStore({
    SharedPreferencesAsync? preferences,
    FlutterSecureStorage? secureStorage,
  })  : _preferences = preferences ?? SharedPreferencesAsync(),
        _secureStorage = secureStorage ?? const FlutterSecureStorage();

  static const _baseUrlKey = 'xiaoyou.base_url';
  static const _deviceIdKey = 'xiaoyou.device_id';
  static const _appLockKey = 'xiaoyou.app_lock';
  static const _draftKey = 'xiaoyou.chat_draft';
  static const _notificationsKey = 'xiaoyou.notifications';
  static const _notificationSoundKey = 'xiaoyou.notification_sound';
  static const _notificationVibrationKey = 'xiaoyou.notification_vibration';
  static const _notificationPreviewKey = 'xiaoyou.notification_preview';
  static const _notificationExplicitlyDisabledKey =
      'xiaoyou.notification_explicitly_disabled';
  static const _fontScaleKey = 'xiaoyou.font_scale';
  static const _compactMessagesKey = 'xiaoyou.compact_messages';
  static const _paletteKey = 'xiaoyou.palette';
  static const _bubbleRadiusKey = 'xiaoyou.bubble_radius';
  static const _tokenKey = 'xiaoyou.connection_token';

  final SharedPreferencesAsync _preferences;
  final FlutterSecureStorage _secureStorage;

  Future<SavedConnection?> loadConnection() async {
    final baseUrl = (await _preferences.getString(_baseUrlKey))?.trim() ?? '';
    final deviceId = (await _preferences.getString(_deviceIdKey))?.trim() ?? '';
    if (baseUrl.isEmpty || deviceId.isEmpty) {
      return null;
    }
    return SavedConnection(
      baseUrl: baseUrl,
      deviceId: deviceId,
      appLockEnabled: await _preferences.getBool(_appLockKey) ?? false,
    );
  }

  Future<String?> readToken() async {
    final token = (await _secureStorage.read(key: _tokenKey))?.trim() ?? '';
    return token.isEmpty ? null : token;
  }

  Future<void> saveConnection(
    SavedConnection connection,
    String token,
  ) async {
    await _secureStorage.write(key: _tokenKey, value: token);
    await _preferences.setString(_baseUrlKey, connection.baseUrl);
    await _preferences.setString(_deviceIdKey, connection.deviceId);
    await _preferences.setBool(_appLockKey, connection.appLockEnabled);
  }

  Future<void> setAppLockEnabled(bool enabled) async {
    await _preferences.setBool(_appLockKey, enabled);
  }

  Future<String> readDraft() async {
    return (await _preferences.getString(_draftKey)) ?? '';
  }

  Future<void> saveDraft(String value) async {
    if (value.isEmpty) {
      await _preferences.remove(_draftKey);
      return;
    }
    await _preferences.setString(_draftKey, value);
  }

  Future<AppPreferences> loadPreferences() async {
    final fontScale = (await _preferences.getDouble(_fontScaleKey) ?? 1)
        .clamp(0.9, 1.2)
        .toDouble();
    final bubbleRadius = (await _preferences.getDouble(_bubbleRadiusKey) ?? 19)
        .clamp(10, 24)
        .toDouble();
    final palette = await _preferences.getString(_paletteKey) ?? 'rose';
    return AppPreferences(
      notificationsEnabled:
          await _preferences.getBool(_notificationsKey) ?? false,
      notificationSound:
          await _preferences.getBool(_notificationSoundKey) ?? true,
      notificationVibration:
          await _preferences.getBool(_notificationVibrationKey) ?? true,
      notificationPreview:
          await _preferences.getBool(_notificationPreviewKey) ?? true,
      notificationExplicitlyDisabled:
          await _preferences.getBool(_notificationExplicitlyDisabledKey) ??
              false,
      fontScale: fontScale,
      compactMessages: await _preferences.getBool(_compactMessagesKey) ?? false,
      palette:
          const {'rose', 'lilac', 'peach'}.contains(palette) ? palette : 'rose',
      bubbleRadius: bubbleRadius,
    );
  }

  Future<void> savePreferences(AppPreferences preferences) async {
    await _preferences.setBool(
      _notificationsKey,
      preferences.notificationsEnabled,
    );
    await _preferences.setBool(
      _notificationSoundKey,
      preferences.notificationSound,
    );
    await _preferences.setBool(
      _notificationVibrationKey,
      preferences.notificationVibration,
    );
    await _preferences.setBool(
      _notificationPreviewKey,
      preferences.notificationPreview,
    );
    await _preferences.setBool(
      _notificationExplicitlyDisabledKey,
      preferences.notificationExplicitlyDisabled,
    );
    await _preferences.setDouble(_fontScaleKey, preferences.fontScale);
    await _preferences.setBool(
      _compactMessagesKey,
      preferences.compactMessages,
    );
    await _preferences.setString(_paletteKey, preferences.palette);
    await _preferences.setDouble(
      _bubbleRadiusKey,
      preferences.bubbleRadius,
    );
  }

  Future<void> clear() async {
    await _secureStorage.delete(key: _tokenKey);
    await _preferences.remove(_baseUrlKey);
    await _preferences.remove(_deviceIdKey);
    await _preferences.remove(_appLockKey);
    await _preferences.remove(_draftKey);
    await _preferences.remove(_notificationsKey);
    await _preferences.remove(_notificationSoundKey);
    await _preferences.remove(_notificationVibrationKey);
    await _preferences.remove(_notificationPreviewKey);
    await _preferences.remove(_notificationExplicitlyDisabledKey);
    await _preferences.remove(_fontScaleKey);
    await _preferences.remove(_compactMessagesKey);
    await _preferences.remove(_paletteKey);
    await _preferences.remove(_bubbleRadiusKey);
  }
}

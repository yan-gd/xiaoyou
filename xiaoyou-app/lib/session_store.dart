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

class SessionStore {
  SessionStore({
    SharedPreferencesAsync? preferences,
    FlutterSecureStorage? secureStorage,
  })  : _preferences = preferences ?? SharedPreferencesAsync(),
        _secureStorage = secureStorage ?? const FlutterSecureStorage();

  static const _baseUrlKey = 'xiaoyou.base_url';
  static const _deviceIdKey = 'xiaoyou.device_id';
  static const _appLockKey = 'xiaoyou.app_lock';
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

  Future<void> clear() async {
    await _secureStorage.delete(key: _tokenKey);
    await _preferences.remove(_baseUrlKey);
    await _preferences.remove(_deviceIdKey);
    await _preferences.remove(_appLockKey);
  }
}

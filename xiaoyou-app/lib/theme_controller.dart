import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _themePreferenceKey = 'xiaoyou_theme_mode';
const _legacyThemePreferenceKey = 'xiaoyou_dark_mode';

final ValueNotifier<ThemeMode> xiaoyouThemeMode =
    ValueNotifier<ThemeMode>(ThemeMode.system);

Future<void> loadXiaoyouThemeMode() async {
  final preferences = await SharedPreferences.getInstance();
  final stored = preferences.getString(_themePreferenceKey);
  final savedMode = switch (stored) {
    'light' => ThemeMode.light,
    'dark' => ThemeMode.dark,
    'system' => ThemeMode.system,
    _ => null,
  };
  if (savedMode != null) {
    xiaoyouThemeMode.value = savedMode;
    return;
  }

  if (preferences.containsKey(_legacyThemePreferenceKey)) {
    final oldDark = preferences.getBool(_legacyThemePreferenceKey) ?? false;
    final migrated = oldDark ? ThemeMode.dark : ThemeMode.light;
    xiaoyouThemeMode.value = migrated;
    await preferences.setString(
      _themePreferenceKey,
      migrated == ThemeMode.dark ? 'dark' : 'light',
    );
    return;
  }

  // New installs follow the device appearance by default.
  xiaoyouThemeMode.value = ThemeMode.system;
}

Future<void> setXiaoyouThemeMode(ThemeMode mode) async {
  if (xiaoyouThemeMode.value != mode) {
    xiaoyouThemeMode.value = mode;
  }
  final preferences = await SharedPreferences.getInstance();
  final stored = switch (mode) {
    ThemeMode.system => 'system',
    ThemeMode.light => 'light',
    ThemeMode.dark => 'dark',
  };
  await preferences.setString(_themePreferenceKey, stored);
}

Future<void> setXiaoyouDarkMode(bool enabled) =>
    setXiaoyouThemeMode(enabled ? ThemeMode.dark : ThemeMode.light);

bool xiaoyouIsDark(BuildContext context) =>
    Theme.of(context).brightness == Brightness.dark;

Color xiaoyouPageSurface(BuildContext context) =>
    xiaoyouIsDark(context) ? const Color(0xff0f0f12) : const Color(0xfffffbfd);

Color xiaoyouElevatedSurface(BuildContext context) =>
    xiaoyouIsDark(context) ? const Color(0xff1b1a1f) : Colors.white;

Color xiaoyouCardSurface(BuildContext context) =>
    xiaoyouIsDark(context) ? const Color(0xff232127) : const Color(0xfffffcfe);

Color xiaoyouSoftSurface(BuildContext context) =>
    xiaoyouIsDark(context) ? const Color(0xff2b292f) : const Color(0xfff6f4f7);

Color xiaoyouPrimaryText(BuildContext context) =>
    xiaoyouIsDark(context) ? const Color(0xfff5f2f4) : const Color(0xff272327);

Color xiaoyouSecondaryText(BuildContext context) =>
    xiaoyouIsDark(context) ? const Color(0xffaaa3a9) : const Color(0xff81787e);

Color xiaoyouHairline(BuildContext context) =>
    xiaoyouIsDark(context) ? const Color(0xff3b383f) : const Color(0xffe9e3e7);

Color xiaoyouGlassBorder(BuildContext context) =>
    xiaoyouIsDark(context) ? const Color(0x24ffffff) : const Color(0xdfffffff);

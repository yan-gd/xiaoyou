import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _themePreferenceKey = 'xiaoyou_dark_mode';

final ValueNotifier<bool> xiaoyouDarkMode = ValueNotifier<bool>(false);

Future<void> loadXiaoyouThemeMode() async {
  final preferences = await SharedPreferences.getInstance();
  xiaoyouDarkMode.value = preferences.getBool(_themePreferenceKey) ?? false;
}

Future<void> setXiaoyouDarkMode(bool enabled) async {
  if (xiaoyouDarkMode.value != enabled) {
    xiaoyouDarkMode.value = enabled;
  }
  final preferences = await SharedPreferences.getInstance();
  await preferences.setBool(_themePreferenceKey, enabled);
}

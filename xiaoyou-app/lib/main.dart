import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

import 'chat_screen.dart';
import 'legal.dart';
import 'theme_controller.dart';

export 'chat_models.dart';
export 'xiaoyou_api.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Draw the local privacy/startup UI immediately. Startup preferences and
  // session/network restoration must never hold Android's first Flutter frame.
  runApp(const XiaoyouApp());
  unawaited(_loadStartupPreferences());
}

Future<void> _loadStartupPreferences() async {
  try {
    await loadXiaoyouThemeMode().timeout(const Duration(seconds: 2));
  } catch (_) {
    // Keep the in-memory default theme and let the App remain usable even if
    // SharedPreferences is temporarily slow/unavailable on a vendor ROM.
  }
}

class XiaoyouApp extends StatelessWidget {
  const XiaoyouApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<bool>(
      valueListenable: xiaoyouDarkMode,
      builder: (context, darkMode, _) {
        return MaterialApp(
          title: '小悠',
          debugShowCheckedModeBanner: false,
          theme: _buildXiaoyouTheme(Brightness.light),
          darkTheme: _buildXiaoyouTheme(Brightness.dark),
          themeMode: darkMode ? ThemeMode.dark : ThemeMode.light,
          home: const PrivacyConsentGate(child: ChatScreen()),
        );
      },
    );
  }
}

ThemeData _buildXiaoyouTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final seed = dark ? const Color(0xff8d7cf4) : const Color(0xff9f4f79);
  final surface = dark ? const Color(0xff15131f) : const Color(0xfffffbfd);
  final foreground = dark ? const Color(0xfff4f0fb) : const Color(0xff30252b);
  final border = dark ? const Color(0xff373147) : const Color(0xffeee2e9);

  final colorScheme = ColorScheme.fromSeed(
    seedColor: seed,
    brightness: brightness,
    surface: surface,
  );

  final baseTextTheme =
      dark ? ThemeData.dark().textTheme : ThemeData.light().textTheme;

  return ThemeData(
    brightness: brightness,
    colorScheme: colorScheme,
    scaffoldBackgroundColor: surface,
    useMaterial3: true,
    fontFamilyFallback: const ['Noto Sans CJK SC', 'sans-serif'],
    splashFactory: InkSparkle.splashFactory,
    pageTransitionsTheme: const PageTransitionsTheme(
      builders: {
        TargetPlatform.android: PredictiveBackPageTransitionsBuilder(),
        TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
      },
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: Colors.transparent,
      surfaceTintColor: Colors.transparent,
      modalBarrierColor:
          dark ? const Color(0xa0000000) : const Color(0x6635262f),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(
        foregroundColor:
            dark ? const Color(0xffe9e1ff) : const Color(0xff5c3448),
        highlightColor:
            dark ? const Color(0x227a68d8) : const Color(0x149f4f79),
      ),
    ),
    textTheme: baseTextTheme.apply(
      bodyColor: foreground,
      displayColor: foreground,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: dark ? const Color(0xff211e2d) : const Color(0xf8ffffff),
      contentPadding: const EdgeInsets.symmetric(
        horizontal: 16,
        vertical: 15,
      ),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(20),
        borderSide: BorderSide(color: border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(20),
        borderSide: BorderSide(color: border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(20),
        borderSide: BorderSide(color: seed, width: 1.4),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: seed,
        foregroundColor: Colors.white,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
        ),
        textStyle: const TextStyle(
          fontSize: 15,
          fontWeight: FontWeight.w700,
        ),
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(28),
      ),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: dark ? const Color(0xff312b42) : const Color(0xff4c3440),
      contentTextStyle: const TextStyle(color: Colors.white),
    ),
  );
}

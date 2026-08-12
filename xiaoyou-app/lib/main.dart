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
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: xiaoyouThemeMode,
      builder: (context, themeMode, _) {
        return MaterialApp(
          title: '小悠',
          debugShowCheckedModeBanner: false,
          theme: _buildXiaoyouTheme(Brightness.light),
          darkTheme: _buildXiaoyouTheme(Brightness.dark),
          themeMode: themeMode,
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
    cardTheme: CardThemeData(
      color: dark ? const Color(0xff1d1b20) : Colors.white,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(22),
        side: BorderSide(color: border),
      ),
    ),
    listTileTheme: ListTileThemeData(
      textColor: foreground,
      iconColor: dark ? const Color(0xffd7d0d5) : const Color(0xff5c4b54),
      subtitleTextStyle: TextStyle(
        color: dark ? const Color(0xffaaa3a9) : const Color(0xff87777f),
        fontSize: 12.5,
      ),
    ),
    dividerTheme: DividerThemeData(
      color: border,
      thickness: 0.8,
      space: 1,
    ),
    chipTheme: ChipThemeData(
      backgroundColor: dark ? const Color(0xff242127) : const Color(0xfff7f3f6),
      selectedColor: dark ? const Color(0xff3b3038) : const Color(0xfff3e5ed),
      side: BorderSide(color: border),
      labelStyle: TextStyle(color: foreground),
      secondaryLabelStyle: TextStyle(color: foreground),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: surface,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(28),
      ),
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      elevation: 3,
      backgroundColor: dark ? const Color(0xff242131) : const Color(0xfffffbfd),
      actionTextColor: dark ? const Color(0xffb9adff) : const Color(0xff7568ef),
      disabledActionTextColor:
          dark ? const Color(0xff777184) : const Color(0xffaaa4b0),
      contentTextStyle: TextStyle(
        color: foreground,
        fontSize: 14,
        fontWeight: FontWeight.w500,
        height: 1.35,
      ),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: BorderSide(
          color: dark ? const Color(0xff40394f) : const Color(0xffeee6ee),
        ),
      ),
    ),
  );
}

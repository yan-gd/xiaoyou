import 'package:flutter/material.dart';

import 'chat_screen.dart';

export 'chat_models.dart';
export 'xiaoyou_api.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const XiaoyouApp());
}

class XiaoyouApp extends StatelessWidget {
  const XiaoyouApp({super.key});

  @override
  Widget build(BuildContext context) {
    const seed = Color(0xff8f476f);
    final colorScheme = ColorScheme.fromSeed(
      seedColor: seed,
      brightness: Brightness.light,
      surface: const Color(0xfffff9fb),
    );
    return MaterialApp(
      title: '小悠',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: colorScheme,
        scaffoldBackgroundColor: const Color(0xfffff9fb),
        useMaterial3: true,
        fontFamilyFallback: const ['Noto Sans CJK SC', 'sans-serif'],
        splashFactory: InkSparkle.splashFactory,
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: Colors.white,
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 16,
            vertical: 15,
          ),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(18),
            borderSide: const BorderSide(color: Color(0xffeadfe4)),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(18),
            borderSide: const BorderSide(color: Color(0xffeadfe4)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(18),
            borderSide: const BorderSide(color: seed, width: 1.4),
          ),
        ),
        filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(
            backgroundColor: seed,
            foregroundColor: Colors.white,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(18),
            ),
            textStyle: const TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.w700,
            ),
          ),
        ),
        dialogTheme: DialogThemeData(
          backgroundColor: const Color(0xfffff9fb),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(26),
          ),
        ),
        snackBarTheme: const SnackBarThemeData(
          backgroundColor: Color(0xff4c3440),
          contentTextStyle: TextStyle(color: Colors.white),
        ),
      ),
      home: const ChatScreen(),
    );
  }
}

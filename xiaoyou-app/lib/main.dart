import 'package:flutter/cupertino.dart';
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
    const seed = Color(0xff9f4f79);
    final colorScheme = ColorScheme.fromSeed(
      seedColor: seed,
      brightness: Brightness.light,
      surface: const Color(0xfffffbfd),
    );
    return MaterialApp(
      title: '小悠',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: colorScheme,
        scaffoldBackgroundColor: const Color(0xfffffbfd),
        useMaterial3: true,
        fontFamilyFallback: const ['Noto Sans CJK SC', 'sans-serif'],
        splashFactory: InkSparkle.splashFactory,
        pageTransitionsTheme: const PageTransitionsTheme(
          builders: {
            TargetPlatform.android: PredictiveBackPageTransitionsBuilder(),
            TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
          },
        ),
        bottomSheetTheme: const BottomSheetThemeData(
          backgroundColor: Colors.transparent,
          surfaceTintColor: Colors.transparent,
          modalBarrierColor: Color(0x6635262f),
        ),
        iconButtonTheme: IconButtonThemeData(
          style: IconButton.styleFrom(
            foregroundColor: const Color(0xff5c3448),
            highlightColor: const Color(0x149f4f79),
          ),
        ),
        textTheme: ThemeData.light().textTheme.apply(
              bodyColor: const Color(0xff30252b),
              displayColor: const Color(0xff30252b),
            ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: const Color(0xf8ffffff),
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 16,
            vertical: 15,
          ),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(20),
            borderSide: const BorderSide(color: Color(0xffeee2e9)),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(20),
            borderSide: const BorderSide(color: Color(0xffeee2e9)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(20),
            borderSide: const BorderSide(color: seed, width: 1.4),
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
          backgroundColor: const Color(0xfffffbfd),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(28),
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

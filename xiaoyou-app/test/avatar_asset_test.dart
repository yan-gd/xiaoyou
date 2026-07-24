import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('Xiaoyou avatar is bundled inside the app package', (
    tester,
  ) async {
    final avatar = await rootBundle.load('assets/xiaoyou-avatar.png');

    expect(avatar.lengthInBytes, greaterThan(100000));
  });
}

import 'package:flutter_test/flutter_test.dart';
import 'package:xiaoyou_app/session_store.dart';

void main() {
  group('AppPreferences', () {
    test('keeps the legacy appearance as the default', () {
      const preferences = AppPreferences();

      expect(preferences.palette, 'rose');
      expect(preferences.backgroundStyle, 'aurora');
      expect(preferences.bubbleStyle, 'soft');
      expect(preferences.motionLevel, 'full');
      expect(preferences.showAvatars, isTrue);
      expect(preferences.showMessageTime, isTrue);
    });

    test('copyWith changes one choice without resetting the others', () {
      const original = AppPreferences(
        notificationsEnabled: true,
        palette: 'sage',
        backgroundStyle: 'starlight',
        bubbleStyle: 'glass',
        motionLevel: 'gentle',
        showAvatars: false,
      );

      final changed = original.copyWith(showMessageTime: false);

      expect(changed.notificationsEnabled, isTrue);
      expect(changed.palette, 'sage');
      expect(changed.backgroundStyle, 'starlight');
      expect(changed.bubbleStyle, 'glass');
      expect(changed.motionLevel, 'gentle');
      expect(changed.showAvatars, isFalse);
      expect(changed.showMessageTime, isFalse);
    });
  });
}

import 'package:flutter_test/flutter_test.dart';
import 'package:xiaoyou_app/achievement_models.dart';
import 'package:xiaoyou_app/chat_models.dart';

void main() {
  test('catalog contains 72 unique achievements across six chapters', () {
    final definitions = ChatAchievementCatalog.definitions;

    expect(definitions, hasLength(72));
    expect(definitions.map((achievement) => achievement.id).toSet(),
        hasLength(72));
    for (final chapter in AchievementChapter.values) {
      expect(
        definitions.where((achievement) => achievement.chapter == chapter),
        hasLength(12),
        reason: '${chapter.title} should contain twelve achievements',
      );
    }
  });

  test('relationship stats use structural conversation facts', () {
    final firstMorning = DateTime(2026, 7, 24, 8, 0);
    final secondMorning = DateTime(2026, 7, 25, 8, 0);
    final messages = [
      _message('1', 'user', 'text', firstMorning, text: '早呀'),
      _message(
        '2',
        'assistant',
        'voice',
        firstMorning.add(const Duration(minutes: 2)),
        text: '早上好',
        durationMs: 4200,
      ),
      _message(
        '3',
        'user',
        'image',
        firstMorning.add(const Duration(minutes: 4)),
      ),
      _message('4', 'assistant', 'text', secondMorning, text: '新的一天'),
      _message(
        '5',
        'user',
        'sticker',
        secondMorning.add(const Duration(minutes: 3)),
      ),
    ];

    final stats = RelationshipAchievementStats.fromMessages(
      messages,
      favoriteCount: 2,
      now: DateTime(2026, 7, 25, 12),
    );

    expect(stats.value(AchievementMetric.totalMessages), 5);
    expect(stats.value(AchievementMetric.activeDays), 2);
    expect(stats.value(AchievementMetric.mutualDays), 2);
    expect(stats.value(AchievementMetric.replyTurns), 3);
    expect(stats.value(AchievementMetric.quickReplies), 3);
    expect(stats.value(AchievementMetric.imageMessages), 1);
    expect(stats.value(AchievementMetric.voiceMessages), 1);
    expect(stats.value(AchievementMetric.voiceSeconds), 5);
    expect(stats.value(AchievementMetric.stickerMessages), 1);
    expect(stats.value(AchievementMetric.mediaKinds), 3);
    expect(stats.value(AchievementMetric.bestStreak), 2);
    expect(stats.value(AchievementMetric.favoriteMessages), 2);
  });

  test('cancelled local messages never unlock achievements', () {
    final timestamp = DateTime(2026, 7, 25, 12);
    final stats = RelationshipAchievementStats.fromMessages(
      [
        ChatMessage(
          id: 'cancelled',
          role: 'user',
          kind: 'text',
          text: '没有真正发送',
          createdAt: timestamp.millisecondsSinceEpoch ~/ 1000,
          localState: 'cancelled',
        ),
      ],
      favoriteCount: 0,
      now: timestamp,
    );

    expect(stats.value(AchievementMetric.totalMessages), 0);
    expect(
      ChatAchievementCatalog.definitions.first.unlocked(stats),
      isFalse,
    );
  });
}

ChatMessage _message(
  String id,
  String role,
  String kind,
  DateTime timestamp, {
  String text = '',
  int durationMs = 0,
}) {
  return ChatMessage(
    id: id,
    role: role,
    kind: kind,
    text: text,
    durationMs: durationMs,
    createdAt: timestamp.millisecondsSinceEpoch ~/ 1000,
  );
}

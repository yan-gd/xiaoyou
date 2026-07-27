import 'package:flutter_test/flutter_test.dart';
import 'package:xiaoyou_app/chat_models.dart';
import 'package:xiaoyou_app/relationship_models.dart';

void main() {
  test('locked capsule never exposes a text body through its model', () {
    final entry = RelationshipEntry.fromJson({
      'entry_id': 'capsule-1',
      'kind': 'capsule',
      'status': 'sealed',
      'title': '写给未来',
      'body': {'author': 'user'},
      'event_at': 100,
      'unlock_at': 200,
      'locked': true,
    });

    expect(entry.locked, isTrue);
    expect(entry.body.containsKey('text'), isFalse);
  });

  test('orbit builder maps only structural chat facts', () {
    const messages = [
      ChatMessage(
        id: 'first',
        role: 'user',
        kind: 'text',
        text: '第一次见面',
        createdAt: 100,
      ),
      ChatMessage(
        id: 'photo',
        role: 'assistant',
        kind: 'image',
        text: '今天的照片',
        mediaId: 'media-photo',
        createdAt: 200,
      ),
      ChatMessage(
        id: 'voice',
        role: 'assistant',
        kind: 'voice',
        text: '晚安呀',
        mediaId: 'media-voice',
        createdAt: 300,
      ),
    ];

    final events = RelationshipOrbitBuilder.build(
      messages: messages,
      entries: const [],
      favoriteMessageIds: const {'first'},
    );

    expect(
      events.map((event) => event.kind),
      containsAll({
        RelationshipEventKind.firstLight,
        RelationshipEventKind.photo,
        RelationshipEventKind.voiceMemory,
        RelationshipEventKind.conversation,
      }),
    );
    expect(
      events.firstWhere(
        (event) => event.kind == RelationshipEventKind.photo,
      ).message?.mediaId,
      'media-photo',
    );
  });

  test('server entries become journal capsule and voice stars', () {
    final entries = [
      RelationshipEntry.fromJson({
        'entry_id': 'journal-1',
        'kind': 'journal',
        'status': 'confirmed',
        'title': '我们今天',
        'body': {'summary': '一起聊了实验。'},
        'event_at': 400,
        'unlock_at': 0,
        'locked': false,
      }),
      RelationshipEntry.fromJson({
        'entry_id': 'capsule-1',
        'kind': 'capsule',
        'status': 'sealed',
        'title': '写给明天',
        'body': {'author': 'user'},
        'event_at': 500,
        'unlock_at': 900,
        'locked': true,
      }),
      RelationshipEntry.fromJson({
        'entry_id': 'voice-1',
        'kind': 'voice_memory',
        'status': 'confirmed',
        'title': '耳边的一会儿',
        'body': {'turn_count': 4},
        'event_at': 600,
        'unlock_at': 0,
        'locked': false,
      }),
    ];

    final events = RelationshipOrbitBuilder.build(
      messages: const [],
      entries: entries,
      favoriteMessageIds: const {},
    );

    expect(events.map((event) => event.kind), [
      RelationshipEventKind.journal,
      RelationshipEventKind.capsule,
      RelationshipEventKind.voiceMemory,
    ]);
    expect(events[1].locked, isTrue);
    expect(events[2].subtitle, contains('4'));
  });
}

import 'package:flutter_test/flutter_test.dart';

import 'package:xiaoyou_app/main.dart';

void main() {
  test('assistant event keeps delivery identity', () {
    final message = ChatMessage.fromJson({
      'event_id': 'event-1',
      'action_id': 'action-1',
      'kind': 'text',
      'text': '在呀',
      'created_at': 123,
    });

    expect(message.fromXiaoyou, isTrue);
    expect(message.id, 'event-1');
    expect(message.actionId, 'action-1');
    expect(message.text, '在呀');
  });
}

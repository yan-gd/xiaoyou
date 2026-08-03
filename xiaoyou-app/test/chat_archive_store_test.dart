import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:xiaoyou_app/chat_archive_store.dart';
import 'package:xiaoyou_app/chat_models.dart';

void main() {
  test('local archive keeps only delivered facts and preserves media metadata',
      () async {
    final root = await Directory.systemTemp.createTemp(
      'xiaoyou-chat-archive-',
    );
    final store = ChatArchiveStore(rootProvider: () async => root);
    addTearDown(() => root.delete(recursive: true));

    await store.replace('https://xiaoyou.example/app', const [
      ChatMessage(
        id: 'old-image',
        role: 'assistant',
        kind: 'image',
        mediaId: 'media-1',
        mimeType: 'image/png',
        createdAt: 10,
      ),
      ChatMessage(
        id: 'failed-local',
        role: 'user',
        kind: 'text',
        text: 'not delivered',
        createdAt: 11,
        localState: 'failed',
      ),
    ]);

    final restored = await store.load('https://xiaoyou.example/app');
    expect(restored.map((message) => message.id), ['old-image']);
    expect(restored.single.mediaId, 'media-1');
    expect(restored.single.mimeType, 'image/png');
  });

  test('message merge is deterministic and newer source wins by id', () {
    final merged = mergeChatMessages(const [
      ChatMessage(
        id: 'same',
        role: 'user',
        kind: 'text',
        text: 'local',
        createdAt: 20,
      ),
      ChatMessage(
        id: 'older',
        role: 'assistant',
        kind: 'text',
        text: 'first',
        createdAt: 10,
      ),
      ChatMessage(
        id: 'same',
        role: 'user',
        kind: 'text',
        text: 'server',
        createdAt: 20,
      ),
    ]);

    expect(merged.map((message) => message.id), ['older', 'same']);
    expect(merged.last.text, 'server');
  });
}

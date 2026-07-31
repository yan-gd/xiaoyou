import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:xiaoyou_app/chat_models.dart';
import 'package:xiaoyou_app/media_cache_service.dart';
import 'package:xiaoyou_app/xiaoyou_api.dart';

void main() {
  late Directory root;
  late MessageMediaCache cache;

  setUp(() async {
    root = await Directory.systemTemp.createTemp('xiaoyou-media-cache-');
    cache = MessageMediaCache(rootProvider: () async => root);
  });

  tearDown(() async {
    if (await root.exists()) {
      await root.delete(recursive: true);
    }
  });

  test('groups image and voice files by local message date', () async {
    final image = _message(
      id: 'image:one',
      kind: 'image',
      mimeType: 'image/png',
      date: DateTime(2026, 7, 30, 23, 58),
    );
    final voice = _message(
      id: 'voice:one',
      kind: 'voice',
      mimeType: 'audio/wav',
      date: DateTime(2026, 7, 31, 0, 2),
    );

    final imageFile = await cache.ensureCached(
      image,
      () async => MediaPayload(
        bytes: Uint8List.fromList(<int>[1, 2, 3]),
        mimeType: 'image/png',
      ),
    );
    final voiceFile = await cache.ensureCached(
      voice,
      () async => MediaPayload(
        bytes: Uint8List.fromList(<int>[82, 73, 70, 70]),
        mimeType: 'audio/wav',
      ),
    );

    expect(imageFile, isNotNull);
    expect(voiceFile, isNotNull);
    expect(
      imageFile!.path,
      contains(
        ['2026-07-30', 'images'].join(Platform.pathSeparator),
      ),
    );
    expect(
      voiceFile!.path,
      contains(
        ['2026-07-31', 'voice'].join(Platform.pathSeparator),
      ),
    );
    expect(imageFile.path, endsWith('.png'));
    expect(voiceFile.path, endsWith('.wav'));
  });

  test('deduplicates concurrent downloads for the same message', () async {
    final message = _message(
      id: 'shared-media',
      kind: 'voice',
      mimeType: 'audio/mpeg',
      date: DateTime(2026, 7, 31, 12),
    );
    var loads = 0;

    Future<MediaPayload> loader() async {
      loads += 1;
      await Future<void>.delayed(const Duration(milliseconds: 20));
      return MediaPayload(
        bytes: Uint8List.fromList(<int>[1, 2, 3, 4]),
        mimeType: 'audio/mpeg',
      );
    }

    final files = await Future.wait(<Future<File?>>[
      cache.ensureCached(message, loader),
      cache.ensureCached(message, loader),
      cache.ensureCached(message, loader),
    ]);

    expect(loads, 1);
    expect(files.every((file) => file?.path == files.first?.path), isTrue);
    expect(await files.first!.length(), 4);
  });

  test('does not persist empty or failed media', () async {
    final message = _message(
      id: 'empty-media',
      kind: 'image',
      mimeType: 'image/jpeg',
      date: DateTime(2026, 7, 31, 12),
    );
    final file = await cache.ensureCached(
      message,
      () async => MediaPayload(
        bytes: Uint8List(0),
        mimeType: 'image/jpeg',
      ),
    );

    expect(file, isNull);
    expect(
        await root.list(recursive: true).where((item) => item is File).toList(),
        isEmpty);
  });
}

ChatMessage _message({
  required String id,
  required String kind,
  required String mimeType,
  required DateTime date,
}) {
  return ChatMessage(
    id: id,
    role: 'assistant',
    kind: kind,
    mediaId: 'media-$id',
    mimeType: mimeType,
    createdAt: date.millisecondsSinceEpoch ~/ 1000,
  );
}

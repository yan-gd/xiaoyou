import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

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

  test('history restores every server page in chronological order', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    var requests = 0;
    final subscription = server.listen((request) async {
      expect(request.uri.path, '/v1/history');
      expect(request.uri.queryParameters['device_id'], 'test-device');
      expect(request.uri.queryParameters['limit'], '300');
      requests += 1;
      final responseBody = requests == 1
          ? '{"messages":[{"id":"new","role":"assistant","kind":"image",'
              '"media_id":"media-new","mime_type":"image/png",'
              '"created_at":20}],"last_event_sequence":9,'
              '"has_more":true,"next_cursor":"older-page"}'
          : '{"messages":[{"id":"old","role":"user","kind":"text",'
              '"text":"old message","created_at":10}],'
              '"last_event_sequence":9,"has_more":false,'
              '"next_cursor":""}';
      if (requests == 2) {
        expect(request.uri.queryParameters['cursor'], 'older-page');
      }
      final encoded = utf8.encode(responseBody);
      request.response
        ..headers.contentType = ContentType.json
        ..contentLength = encoded.length
        ..add(encoded);
      await request.response.close();
    });
    final api = XiaoyouApi(
      baseUrl: 'http://${server.address.address}:${server.port}',
      token: 'test-token-with-at-least-24-characters',
      deviceId: 'test-device',
    );

    try {
      final history = await api.history();
      expect(requests, 2);
      expect(history.messages.map((message) => message.id), ['old', 'new']);
      expect(history.messages.last.mediaId, 'media-new');
      expect(history.lastEventSequence, 9);
    } finally {
      api.close();
      await subscription.cancel();
      await server.close(force: true);
    }
  });

  test('history stops paging once the newest page overlaps local archive',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    var requests = 0;
    final subscription = server.listen((request) async {
      requests += 1;
      final responseBody =
          '{"messages":[{"id":"new","role":"assistant","kind":"text",'
          '"text":"new message","created_at":20},'
          '{"id":"known","role":"user","kind":"text",'
          '"text":"known message","created_at":10}],'
          '"last_event_sequence":12,"has_more":true,'
          '"next_cursor":"must-not-be-requested"}';
      final encoded = utf8.encode(responseBody);
      request.response
        ..headers.contentType = ContentType.json
        ..contentLength = encoded.length
        ..add(encoded);
      await request.response.close();
    });
    final api = XiaoyouApi(
      baseUrl: 'http://${server.address.address}:${server.port}',
      token: 'test-token-with-at-least-24-characters',
      deviceId: 'test-device',
    );

    try {
      final history = await api.history(
        stopAfterMessageIds: {'known'},
      );
      expect(requests, 1);
      expect(history.messages.map((message) => message.id), ['known', 'new']);
      expect(history.lastEventSequence, 12);
    } finally {
      api.close();
      await subscription.cancel();
      await server.close(force: true);
    }
  });

  test('API reuses its HTTP connection across requests', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final remotePorts = <int>{};
    final subscription = server.listen((request) async {
      final remotePort = request.connectionInfo?.remotePort;
      if (remotePort != null) {
        remotePorts.add(remotePort);
      }
      const responseBody = '{"ok":true}';
      request.response.persistentConnection = true;
      request.response.headers.contentType = ContentType.json;
      request.response.headers.set(HttpHeaders.connectionHeader, 'keep-alive');
      request.response.contentLength = responseBody.length;
      request.response.write(responseBody);
      await request.response.close();
    });
    final api = XiaoyouApi(
      baseUrl: 'http://${server.address.address}:${server.port}',
      token: 'test-token-with-at-least-24-characters',
      deviceId: 'test-device',
    );

    try {
      await api.health();
      await Future<void>.delayed(const Duration(milliseconds: 20));
      await api.health();
      expect(remotePorts, hasLength(1));
    } finally {
      api.close();
      await subscription.cancel();
      await server.close(force: true);
    }
  });

  test('voice upload carries immutable identity and audio metadata', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    late Uint8List received;
    final subscription = server.listen((request) async {
      final bytes = BytesBuilder(copy: false);
      await for (final chunk in request) {
        bytes.add(chunk);
      }
      received = bytes.takeBytes();
      expect(request.uri.path, '/v1/voice-messages');
      expect(request.headers.value('X-Message-Id'), 'voice-1');
      expect(request.headers.value('X-Device-Id'), 'test-device');
      expect(request.headers.contentType?.mimeType, 'audio/mp4');
      const responseBody = '{"accepted":true,"duplicate":false,"text":"我想你了",'
          '"media_id":"media-1","mime_type":"audio/mp4",'
          '"duration_ms":2300}';
      final encoded = utf8.encode(responseBody);
      request.response
        ..statusCode = HttpStatus.accepted
        ..headers.contentType = ContentType.json
        ..contentLength = encoded.length
        ..add(encoded);
      await request.response.close();
    });
    final api = XiaoyouApi(
      baseUrl: 'http://${server.address.address}:${server.port}',
      token: 'test-token-with-at-least-24-characters',
      deviceId: 'test-device',
    );

    try {
      final result = await api.sendVoice(
        messageId: 'voice-1',
        audioBytes: Uint8List.fromList([1, 2, 3, 4]),
        mimeType: 'audio/mp4',
        durationMs: 2300,
        sequence: 4,
      );
      expect(received, [1, 2, 3, 4]);
      expect(result.accepted, isTrue);
      expect(result.text, '我想你了');
      expect(result.mediaId, 'media-1');
      expect(result.durationMs, 2300);
    } finally {
      api.close();
      await subscription.cancel();
      await server.close(force: true);
    }
  });

  test('O2 voice-room turn stays on its independent WAV endpoint', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    late Uint8List received;
    final subscription = server.listen((request) async {
      final bytes = BytesBuilder(copy: false);
      await for (final chunk in request) {
        bytes.add(chunk);
      }
      received = bytes.takeBytes();
      expect(request.uri.path, '/v1/voice-rooms/room-1/turns');
      expect(request.headers.value('X-Turn-Id'), 'turn-1');
      expect(request.headers.value('X-Device-Id'), 'test-device');
      expect(request.headers.value('X-Audio-Duration-Ms'), '1800');
      expect(request.headers.contentType?.mimeType, 'audio/wav');
      const responseBody = '{"accepted":true,"duplicate":false,"turn":{'
          '"turn_id":"turn-1","turn_index":1,'
          '"user_text":"我回来了","assistant_text":"欢迎回来。",'
          '"user_duration_ms":1800,"assistant_duration_ms":1200,'
          '"audio_media_id":"voice-wav-1","audio_mime_type":"audio/wav",'
          '"created_at":100,"memory_status":"pending"}}';
      final encoded = utf8.encode(responseBody);
      request.response
        ..statusCode = HttpStatus.ok
        ..headers.contentType = ContentType.json
        ..contentLength = encoded.length
        ..add(encoded);
      await request.response.close();
    });
    final api = XiaoyouApi(
      baseUrl: 'http://${server.address.address}:${server.port}',
      token: 'test-token-with-at-least-24-characters',
      deviceId: 'test-device',
    );

    try {
      final result = await api.sendVoiceRoomTurn(
        roomId: 'room-1',
        turnId: 'turn-1',
        audioBytes: Uint8List.fromList([82, 73, 70, 70]),
        mimeType: 'audio/wav',
        durationMs: 1800,
      );
      expect(received, [82, 73, 70, 70]);
      expect(result.accepted, isTrue);
      expect(result.turn.userText, '我回来了');
      expect(result.turn.assistantText, '欢迎回来。');
      expect(result.turn.audioMediaId, 'voice-wav-1');
      expect(result.turn.memoryStatus, 'pending');
    } finally {
      api.close();
      await subscription.cancel();
      await server.close(force: true);
    }
  });

  test('O2 realtime room streams PCM, events, and playback truncation',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final paths = <String>[];
    final subscription = server.listen((request) async {
      paths.add(request.uri.path);
      expect(
        request.headers.value(HttpHeaders.authorizationHeader),
        'Bearer test-token-with-at-least-24-characters',
      );
      late String responseBody;
      if (request.uri.path.endsWith('/audio')) {
        final bytes = BytesBuilder(copy: false);
        await for (final chunk in request) {
          bytes.add(chunk);
        }
        expect(bytes.takeBytes(), [1, 0, 2, 0]);
        expect(request.headers.value('X-Device-Id'), 'test-device');
        expect(request.headers.contentType?.mimeType, 'audio/pcm');
        expect(request.persistentConnection, isTrue);
        request.response.statusCode = HttpStatus.accepted;
        request.response.headers.contentType = ContentType.binary;
        request.response.add([0xff, 0xfe, 0xfd]);
        await request.response.close();
        return;
      } else if (request.uri.path.endsWith('/events')) {
        expect(request.uri.queryParameters['after'], '7');
        expect(request.uri.queryParameters['device_id'], 'test-device');
        responseBody = '{"events":[{"sequence":8,'
            '"type":"user_speech_started","interrupted":true,'
            '"reply_id":"reply-1"}]}';
      } else {
        final body = jsonDecode(await utf8.decoder.bind(request).join())
            as Map<String, dynamic>;
        expect(body['device_id'], 'test-device');
        expect(body['reply_id'], 'reply-1');
        expect(body['audio_end_ms'], 640);
        responseBody = '{"accepted":true}';
      }
      final encoded = utf8.encode(responseBody);
      request.response
        ..headers.contentType = ContentType.json
        ..contentLength = encoded.length
        ..add(encoded);
      await request.response.close();
    });
    final api = XiaoyouApi(
      baseUrl: 'http://${server.address.address}:${server.port}',
      token: 'test-token-with-at-least-24-characters',
      deviceId: 'test-device',
    );

    try {
      await api.sendVoiceRoomAudio(
        roomId: 'room-live',
        pcm: Uint8List.fromList([1, 0, 2, 0]),
      );
      final events = await api.voiceRoomEvents(
        roomId: 'room-live',
        after: 7,
      );
      final truncated = await api.truncateVoiceRoomReply(
        roomId: 'room-live',
        replyId: 'reply-1',
        audioEndMs: 640,
      );
      expect(events.single.sequence, 8);
      expect(events.single.type, 'user_speech_started');
      expect(events.single.payload['interrupted'], isTrue);
      expect(truncated, isTrue);
      expect(paths, [
        '/v1/voice-rooms/room-live/audio',
        '/v1/voice-rooms/room-live/events',
        '/v1/voice-rooms/room-live/truncate',
      ]);
    } finally {
      api.close();
      await subscription.cancel();
      await server.close(force: true);
    }
  });

  test('image upload carries identity, kind and raw image bytes', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    late Uint8List received;
    final subscription = server.listen((request) async {
      final bytes = BytesBuilder(copy: false);
      await for (final chunk in request) {
        bytes.add(chunk);
      }
      received = bytes.takeBytes();
      expect(request.uri.path, '/v1/image-messages');
      expect(request.headers.value('X-Message-Id'), 'sticker-1');
      expect(request.headers.value('X-Message-Kind'), 'sticker');
      expect(request.headers.value('X-Device-Id'), 'test-device');
      expect(request.headers.contentType?.mimeType, 'image/png');
      const responseBody = '{"accepted":true,"duplicate":false,'
          '"kind":"sticker","media_id":"media-image-1",'
          '"mime_type":"image/png"}';
      final encoded = utf8.encode(responseBody);
      request.response
        ..statusCode = HttpStatus.accepted
        ..headers.contentType = ContentType.json
        ..contentLength = encoded.length
        ..add(encoded);
      await request.response.close();
    });
    final api = XiaoyouApi(
      baseUrl: 'http://${server.address.address}:${server.port}',
      token: 'test-token-with-at-least-24-characters',
      deviceId: 'test-device',
    );

    try {
      final result = await api.sendImage(
        messageId: 'sticker-1',
        imageBytes: Uint8List.fromList([137, 80, 78, 71]),
        mimeType: 'image/png',
        kind: 'sticker',
        sequence: 5,
      );
      expect(received, [137, 80, 78, 71]);
      expect(result.accepted, isTrue);
      expect(result.kind, 'sticker');
      expect(result.mediaId, 'media-image-1');
    } finally {
      api.close();
      await subscription.cancel();
      await server.close(force: true);
    }
  });

  test('authenticated media download returns playable bytes', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final subscription = server.listen((request) async {
      expect(request.uri.path, '/v1/media/media-voice');
      expect(request.uri.queryParameters['device_id'], 'test-device');
      expect(
        request.headers.value(HttpHeaders.authorizationHeader),
        'Bearer test-token-with-at-least-24-characters',
      );
      request.response
        ..headers.contentType = ContentType('audio', 'wav')
        ..contentLength = 3
        ..add([7, 8, 9]);
      await request.response.close();
    });
    final api = XiaoyouApi(
      baseUrl: 'http://${server.address.address}:${server.port}',
      token: 'test-token-with-at-least-24-characters',
      deviceId: 'test-device',
    );

    try {
      final media = await api.downloadMedia('media-voice');
      expect(media.mimeType, 'audio/wav');
      expect(media.bytes, [7, 8, 9]);
    } finally {
      api.close();
      await subscription.cancel();
      await server.close(force: true);
    }
  });
}

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

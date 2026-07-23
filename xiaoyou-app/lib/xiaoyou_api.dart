import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'chat_models.dart';

class XiaoyouApi {
  XiaoyouApi({
    required String baseUrl,
    required this.token,
    required this.deviceId,
  }) : baseUri = Uri.parse(baseUrl.replaceFirst(RegExp(r'/+$'), '')) {
    _client.connectionTimeout = const Duration(seconds: 12);
    _client.idleTimeout = const Duration(seconds: 30);
    _client.maxConnectionsPerHost = 4;
  }

  final Uri baseUri;
  final String token;
  final String deviceId;
  final HttpClient _client = HttpClient();

  Future<void> health() async {
    await _request('GET', '/v1/health', authenticated: false);
  }

  Future<void> registerDevice() async {
    await _request(
      'POST',
      '/v1/devices',
      body: {
        'device_id': deviceId,
        'platform': Platform.isAndroid
            ? 'android'
            : (Platform.isIOS ? 'ios' : Platform.operatingSystem),
      },
    );
  }

  Future<ChatHistory> history() async {
    final payload = await _request(
      'GET',
      '/v1/history',
      query: {'device_id': deviceId, 'limit': '200'},
    );
    final values = payload['messages'];
    if (values is! List) {
      return ChatHistory(
        messages: const [],
        lastEventSequence: asInt(payload['last_event_sequence']),
      );
    }
    final messages = values
        .whereType<Map>()
        .map((value) => ChatMessage.fromJson(value.cast<String, dynamic>()))
        .toList();
    return ChatHistory(
      messages: messages,
      lastEventSequence: asInt(payload['last_event_sequence']),
    );
  }

  Future<bool> sendText({
    required String messageId,
    required String text,
    required int sequence,
  }) async {
    final payload = await _request(
      'POST',
      '/v1/messages',
      body: {
        'message_id': messageId,
        'device_id': deviceId,
        'client_sequence': sequence,
        'created_at': DateTime.now().millisecondsSinceEpoch ~/ 1000,
        'text': text,
      },
    );
    return payload['accepted'] == true;
  }

  Future<VoiceSendResult> sendVoice({
    required String messageId,
    required Uint8List audioBytes,
    required String mimeType,
    required int durationMs,
    required int sequence,
  }) async {
    final uri = _uri('/v1/voice-messages');
    final request = await _client.openUrl('POST', uri);
    request.persistentConnection = true;
    request.headers
      ..set(HttpHeaders.acceptHeader, 'application/json')
      ..set(HttpHeaders.authorizationHeader, 'Bearer $token')
      ..set(HttpHeaders.contentTypeHeader, mimeType)
      ..set('X-Message-Id', messageId)
      ..set('X-Device-Id', deviceId)
      ..set('X-Audio-Duration-Ms', '$durationMs')
      ..set('X-Client-Sequence', '$sequence')
      ..set(
        'X-Client-Created-At',
        '${DateTime.now().millisecondsSinceEpoch ~/ 1000}',
      )
      ..contentLength = audioBytes.length;
    request.add(audioBytes);
    final response = await request.close().timeout(
          const Duration(seconds: 75),
        );
    final payload = await _jsonResponse(
      response,
      uri,
      timeout: const Duration(seconds: 75),
    );
    return VoiceSendResult(
      accepted: payload['accepted'] == true,
      duplicate: payload['duplicate'] == true,
      text: '${payload['text'] ?? ''}',
      mediaId: '${payload['media_id'] ?? ''}',
      mimeType: '${payload['mime_type'] ?? mimeType}',
      durationMs: asInt(payload['duration_ms']),
    );
  }

  Future<List<Map<String, dynamic>>> eventsAfter(int sequence) async {
    final payload = await _request(
      'GET',
      '/v1/events',
      query: {'device_id': deviceId, 'after': '$sequence', 'limit': '100'},
    );
    final events = payload['events'];
    if (events is! List) {
      return const [];
    }
    return events
        .whereType<Map>()
        .map((value) => value.cast<String, dynamic>())
        .toList();
  }

  Future<void> acknowledge(String actionId) async {
    await _request(
      'POST',
      '/v1/deliveries/$actionId',
      body: {'device_id': deviceId, 'terminal_status': 'complete'},
    );
  }

  String mediaUrl(String mediaId) {
    return _uri('/v1/media/$mediaId', {'device_id': deviceId}).toString();
  }

  Map<String, String> get mediaHeaders => {'Authorization': 'Bearer $token'};

  Future<MediaPayload> downloadMedia(String mediaId) async {
    final uri = _uri('/v1/media/$mediaId', {'device_id': deviceId});
    final request = await _client.getUrl(uri);
    request.persistentConnection = true;
    request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
    final response = await request.close().timeout(
          const Duration(seconds: 35),
        );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      await response.drain<void>();
      throw HttpException('HTTP ${response.statusCode}', uri: uri);
    }
    final builder = BytesBuilder(copy: false);
    await for (final chunk in response.timeout(const Duration(seconds: 35))) {
      builder.add(chunk);
    }
    final bytes = builder.takeBytes();
    return MediaPayload(
      bytes: bytes,
      mimeType:
          response.headers.contentType?.mimeType ?? 'application/octet-stream',
    );
  }

  void close() {
    _client.close(force: true);
  }

  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Map<String, String>? query,
    Map<String, dynamic>? body,
    bool authenticated = true,
  }) async {
    final request = await _client.openUrl(method, _uri(path, query));
    request.persistentConnection = true;
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (authenticated) {
      request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
    }
    if (body != null) {
      final bytes = utf8.encode(jsonEncode(body));
      request.headers.contentType = ContentType.json;
      request.headers.contentLength = bytes.length;
      request.add(bytes);
    }
    final response = await request.close().timeout(
          const Duration(seconds: 35),
        );
    return _jsonResponse(response, _uri(path, query));
  }

  Future<Map<String, dynamic>> _jsonResponse(
    HttpClientResponse response,
    Uri uri, {
    Duration timeout = const Duration(seconds: 35),
  }) async {
    final responseText = await utf8.decoder.bind(response).join().timeout(
          timeout,
        );
    Map<String, dynamic> payload = {};
    if (responseText.trim().isNotEmpty) {
      final decoded = jsonDecode(responseText);
      if (decoded is Map) {
        payload = decoded.cast<String, dynamic>();
      }
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw HttpException(
        '${payload['error'] ?? 'HTTP ${response.statusCode}'}',
        uri: uri,
      );
    }
    return payload;
  }

  Uri _uri(String path, [Map<String, String>? query]) {
    final normalizedPath = [
      ...baseUri.pathSegments.where((item) => item.isNotEmpty),
      ...path.split('/').where((item) => item.isNotEmpty),
    ];
    return baseUri.replace(
      pathSegments: normalizedPath,
      queryParameters: query,
    );
  }
}

class VoiceSendResult {
  const VoiceSendResult({
    required this.accepted,
    required this.duplicate,
    required this.text,
    required this.mediaId,
    required this.mimeType,
    required this.durationMs,
  });

  final bool accepted;
  final bool duplicate;
  final String text;
  final String mediaId;
  final String mimeType;
  final int durationMs;
}

class MediaPayload {
  const MediaPayload({required this.bytes, required this.mimeType});

  final Uint8List bytes;
  final String mimeType;
}

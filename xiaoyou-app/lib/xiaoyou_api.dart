import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'chat_models.dart';
import 'relationship_models.dart';

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

  Future<Map<String, dynamic>> profile() async {
    final payload = await _request(
      'GET',
      '/v1/profile',
      query: {'device_id': deviceId},
    );
    return payload;
  }

  Future<List<RelationshipEntry>> relationshipEntries() async {
    final payload = await _request(
      'GET',
      '/v1/relationship/entries',
      query: {'device_id': deviceId},
    );
    final values = payload['entries'];
    if (values is! List) {
      return const [];
    }
    return values
        .whereType<Map>()
        .map(
          (value) => RelationshipEntry.fromJson(value.cast<String, dynamic>()),
        )
        .toList();
  }

  Future<DailyJournal> draftDailyJournal(DateTime day) async {
    final payload = await _request(
      'POST',
      '/v1/relationship/journals/draft',
      body: {
        'device_id': deviceId,
        'day': _dateKey(day),
      },
    );
    return DailyJournal.fromEntry(
      RelationshipEntry.fromJson(
        (payload['entry'] as Map).cast<String, dynamic>(),
      ),
    );
  }

  Future<DailyJournal> confirmDailyJournal(
    DailyJournal journal, {
    required String summary,
    required String tomorrowWish,
  }) async {
    final payload = await _request(
      'POST',
      '/v1/relationship/journals/${journal.entry.id}/confirm',
      body: {
        'device_id': deviceId,
        'body': {
          ...journal.entry.body,
          'summary': summary,
          'tomorrow_wish': tomorrowWish,
        },
      },
    );
    return DailyJournal.fromEntry(
      RelationshipEntry.fromJson(
        (payload['entry'] as Map).cast<String, dynamic>(),
      ),
    );
  }

  Future<RelationshipEntry> createTimeCapsule({
    required String title,
    required String text,
    required DateTime unlockAt,
  }) async {
    final payload = await _request(
      'POST',
      '/v1/relationship/capsules',
      body: {
        'device_id': deviceId,
        'title': title,
        'text': text,
        'unlock_at': unlockAt.millisecondsSinceEpoch ~/ 1000,
        'author': 'user',
      },
    );
    return RelationshipEntry.fromJson(
      (payload['entry'] as Map).cast<String, dynamic>(),
    );
  }

  Future<RelationshipEntry> openTimeCapsule(String entryId) async {
    final payload = await _request(
      'POST',
      '/v1/relationship/capsules/$entryId/open',
      body: {'device_id': deviceId},
    );
    return RelationshipEntry.fromJson(
      (payload['entry'] as Map).cast<String, dynamic>(),
    );
  }

  Future<RelationshipEntry> recordVoiceRoomMemory({
    required DateTime startedAt,
    required DateTime endedAt,
    required int turnCount,
    required int durationMs,
  }) async {
    final payload = await _request(
      'POST',
      '/v1/relationship/voice-memories',
      body: {
        'device_id': deviceId,
        'started_at': startedAt.millisecondsSinceEpoch ~/ 1000,
        'ended_at': endedAt.millisecondsSinceEpoch ~/ 1000,
        'turn_count': turnCount,
        'duration_ms': durationMs,
        'title': '耳边的一会儿',
      },
    );
    return RelationshipEntry.fromJson(
      (payload['entry'] as Map).cast<String, dynamic>(),
    );
  }

  Future<VoiceRoomRecord> createVoiceRoom({
    String title = '耳边的一会儿',
  }) async {
    final payload = await _request(
      'POST',
      '/v1/voice-rooms',
      body: {
        'device_id': deviceId,
        'title': title,
      },
    );
    return VoiceRoomRecord.fromJson(
      (payload['room'] as Map).cast<String, dynamic>(),
    );
  }

  Future<List<VoiceRoomRecord>> voiceRooms({int limit = 30}) async {
    final payload = await _request(
      'GET',
      '/v1/voice-rooms',
      query: {
        'device_id': deviceId,
        'limit': '$limit',
      },
    );
    final rooms = payload['rooms'];
    if (rooms is! List) {
      return const [];
    }
    return rooms
        .whereType<Map>()
        .map(
          (value) => VoiceRoomRecord.fromJson(
            value.cast<String, dynamic>(),
          ),
        )
        .toList();
  }

  Future<VoiceRoomRecord> voiceRoom(String roomId) async {
    final payload = await _request(
      'GET',
      '/v1/voice-rooms/$roomId',
      query: {'device_id': deviceId},
    );
    return VoiceRoomRecord.fromJson(
      (payload['room'] as Map).cast<String, dynamic>(),
    );
  }

  Future<VoiceRoomTurnResult> sendVoiceRoomTurn({
    required String roomId,
    required String turnId,
    required Uint8List audioBytes,
    required String mimeType,
    required int durationMs,
  }) async {
    final uri = _uri('/v1/voice-rooms/$roomId/turns');
    final request = await _client.openUrl('POST', uri);
    request.persistentConnection = true;
    request.headers
      ..set(HttpHeaders.acceptHeader, 'application/json')
      ..set(HttpHeaders.authorizationHeader, 'Bearer $token')
      ..set(HttpHeaders.contentTypeHeader, mimeType)
      ..set('X-Turn-Id', turnId)
      ..set('X-Device-Id', deviceId)
      ..set('X-Audio-Duration-Ms', '$durationMs')
      ..contentLength = audioBytes.length;
    request.add(audioBytes);
    final response = await request.close().timeout(
          const Duration(seconds: 120),
        );
    final payload = await _jsonResponse(
      response,
      uri,
      timeout: const Duration(seconds: 120),
    );
    return VoiceRoomTurnResult(
      accepted: payload['accepted'] == true,
      duplicate: payload['duplicate'] == true,
      turn: VoiceRoomTurn.fromJson(
        (payload['turn'] as Map).cast<String, dynamic>(),
      ),
    );
  }

  Future<VoiceRoomRecord> finishVoiceRoom(String roomId) async {
    final payload = await _request(
      'POST',
      '/v1/voice-rooms/$roomId/finish',
      body: {'device_id': deviceId},
    );
    return VoiceRoomRecord.fromJson(
      (payload['room'] as Map).cast<String, dynamic>(),
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

  Future<ImageSendResult> sendImage({
    required String messageId,
    required Uint8List imageBytes,
    required String mimeType,
    required String kind,
    required int sequence,
  }) async {
    final uri = _uri('/v1/image-messages');
    final request = await _client.openUrl('POST', uri);
    request.persistentConnection = true;
    request.headers
      ..set(HttpHeaders.acceptHeader, 'application/json')
      ..set(HttpHeaders.authorizationHeader, 'Bearer $token')
      ..set(HttpHeaders.contentTypeHeader, mimeType)
      ..set('X-Message-Id', messageId)
      ..set('X-Message-Kind', kind == 'sticker' ? 'sticker' : 'image')
      ..set('X-Device-Id', deviceId)
      ..set('X-Client-Sequence', '$sequence')
      ..set(
        'X-Client-Created-At',
        '${DateTime.now().millisecondsSinceEpoch ~/ 1000}',
      )
      ..contentLength = imageBytes.length;
    request.add(imageBytes);
    final response = await request.close().timeout(
          const Duration(seconds: 75),
        );
    final payload = await _jsonResponse(
      response,
      uri,
      timeout: const Duration(seconds: 75),
    );
    return ImageSendResult(
      accepted: payload['accepted'] == true,
      duplicate: payload['duplicate'] == true,
      kind: '${payload['kind'] ?? kind}',
      mediaId: '${payload['media_id'] ?? ''}',
      mimeType: '${payload['mime_type'] ?? mimeType}',
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

  static String _dateKey(DateTime value) =>
      '${value.year.toString().padLeft(4, '0')}-'
      '${value.month.toString().padLeft(2, '0')}-'
      '${value.day.toString().padLeft(2, '0')}';

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

class ImageSendResult {
  const ImageSendResult({
    required this.accepted,
    required this.duplicate,
    required this.kind,
    required this.mediaId,
    required this.mimeType,
  });

  final bool accepted;
  final bool duplicate;
  final String kind;
  final String mediaId;
  final String mimeType;
}

class MediaPayload {
  const MediaPayload({required this.bytes, required this.mimeType});

  final Uint8List bytes;
  final String mimeType;
}

class VoiceRoomRecord {
  const VoiceRoomRecord({
    required this.roomId,
    required this.title,
    required this.status,
    required this.startedAt,
    required this.endedAt,
    required this.turnCount,
    required this.turns,
  });

  factory VoiceRoomRecord.fromJson(Map<String, dynamic> value) {
    final rawTurns = value['turns'];
    return VoiceRoomRecord(
      roomId: '${value['room_id'] ?? ''}',
      title: '${value['title'] ?? '耳边的一会儿'}',
      status: '${value['status'] ?? ''}',
      startedAt: DateTime.fromMillisecondsSinceEpoch(
        asInt(value['started_at']) * 1000,
      ),
      endedAt: asInt(value['ended_at']) > 0
          ? DateTime.fromMillisecondsSinceEpoch(
              asInt(value['ended_at']) * 1000,
            )
          : null,
      turnCount: asInt(value['turn_count']),
      turns: rawTurns is List
          ? rawTurns
              .whereType<Map>()
              .map(
                (item) => VoiceRoomTurn.fromJson(
                  item.cast<String, dynamic>(),
                ),
              )
              .toList()
          : const [],
    );
  }

  final String roomId;
  final String title;
  final String status;
  final DateTime startedAt;
  final DateTime? endedAt;
  final int turnCount;
  final List<VoiceRoomTurn> turns;
}

class VoiceRoomTurn {
  const VoiceRoomTurn({
    required this.turnId,
    required this.turnIndex,
    required this.userText,
    required this.assistantText,
    required this.userDurationMs,
    required this.assistantDurationMs,
    required this.audioMediaId,
    required this.audioMimeType,
    required this.createdAt,
    required this.memoryStatus,
  });

  factory VoiceRoomTurn.fromJson(Map<String, dynamic> value) {
    return VoiceRoomTurn(
      turnId: '${value['turn_id'] ?? ''}',
      turnIndex: asInt(value['turn_index']),
      userText: '${value['user_text'] ?? ''}',
      assistantText: '${value['assistant_text'] ?? ''}',
      userDurationMs: asInt(value['user_duration_ms']),
      assistantDurationMs: asInt(value['assistant_duration_ms']),
      audioMediaId: '${value['audio_media_id'] ?? ''}',
      audioMimeType: '${value['audio_mime_type'] ?? 'audio/wav'}',
      createdAt: DateTime.fromMillisecondsSinceEpoch(
        asInt(value['created_at']) * 1000,
      ),
      memoryStatus: '${value['memory_status'] ?? 'pending'}',
    );
  }

  final String turnId;
  final int turnIndex;
  final String userText;
  final String assistantText;
  final int userDurationMs;
  final int assistantDurationMs;
  final String audioMediaId;
  final String audioMimeType;
  final DateTime createdAt;
  final String memoryStatus;
}

class VoiceRoomTurnResult {
  const VoiceRoomTurnResult({
    required this.accepted,
    required this.duplicate,
    required this.turn,
  });

  final bool accepted;
  final bool duplicate;
  final VoiceRoomTurn turn;
}

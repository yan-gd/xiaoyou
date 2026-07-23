class ChatMessage {
  const ChatMessage({
    required this.id,
    required this.role,
    required this.kind,
    required this.createdAt,
    this.text = '',
    this.actionId = '',
    this.mediaId = '',
    this.remoteUrl = '',
    this.mimeType = '',
    this.durationMs = 0,
    this.localPath = '',
    this.terminalStatus = '',
    this.requestedParts = 0,
    this.localState = 'sent',
  });

  final String id;
  final String role;
  final String kind;
  final String text;
  final String actionId;
  final String mediaId;
  final String remoteUrl;
  final String mimeType;
  final int durationMs;
  final String localPath;
  final String terminalStatus;
  final int requestedParts;
  final int createdAt;
  final String localState;

  bool get fromXiaoyou => role == 'assistant';

  DateTime get timestamp =>
      DateTime.fromMillisecondsSinceEpoch(createdAt * 1000);

  ChatMessage copyWith({
    String? text,
    String? mediaId,
    String? mimeType,
    int? durationMs,
    String? localPath,
    String? localState,
  }) {
    return ChatMessage(
      id: id,
      role: role,
      kind: kind,
      text: text ?? this.text,
      actionId: actionId,
      mediaId: mediaId ?? this.mediaId,
      remoteUrl: remoteUrl,
      mimeType: mimeType ?? this.mimeType,
      durationMs: durationMs ?? this.durationMs,
      localPath: localPath ?? this.localPath,
      terminalStatus: terminalStatus,
      requestedParts: requestedParts,
      createdAt: createdAt,
      localState: localState ?? this.localState,
    );
  }

  factory ChatMessage.fromJson(Map<String, dynamic> value) {
    return ChatMessage(
      id: '${value['id'] ?? value['event_id'] ?? ''}',
      actionId: '${value['action_id'] ?? ''}',
      role: '${value['role'] ?? 'assistant'}',
      kind: '${value['kind'] ?? 'text'}',
      text: '${value['text'] ?? ''}',
      mediaId: '${value['media_id'] ?? ''}',
      remoteUrl: '${value['remote_url'] ?? ''}',
      mimeType: '${value['mime_type'] ?? ''}',
      durationMs: asInt(value['duration_ms']),
      terminalStatus: '${value['terminal_status'] ?? ''}',
      requestedParts: asInt(value['requested_parts']),
      createdAt: asInt(value['created_at']),
    );
  }
}

class ChatHistory {
  const ChatHistory({required this.messages, required this.lastEventSequence});

  final List<ChatMessage> messages;
  final int lastEventSequence;
}

int asInt(Object? value) {
  if (value is int) {
    return value;
  }
  return int.tryParse('$value') ?? 0;
}

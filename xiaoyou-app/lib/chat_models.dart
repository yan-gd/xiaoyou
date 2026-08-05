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
    this.streaming = false,
    this.streamToken = '',
    this.localPath = '',
    this.terminalStatus = '',
    this.requestedParts = 0,
    this.localState = 'sent',
    this.aiGenerated = false,
    this.aiLabel = '',
    this.aiProviderName = '',
    this.aiProviderCode = '',
    this.aiContentId = '',
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
  final bool streaming;
  final String streamToken;
  final String localPath;
  final String terminalStatus;
  final int requestedParts;
  final int createdAt;
  final String localState;
  final bool aiGenerated;
  final String aiLabel;
  final String aiProviderName;
  final String aiProviderCode;
  final String aiContentId;

  bool get fromXiaoyou => role == 'assistant';

  DateTime get timestamp =>
      DateTime.fromMillisecondsSinceEpoch(createdAt * 1000);

  ChatMessage copyWith({
    String? text,
    String? mediaId,
    String? mimeType,
    int? durationMs,
    bool? streaming,
    String? streamToken,
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
      streaming: streaming ?? this.streaming,
      streamToken: streamToken ?? this.streamToken,
      localPath: localPath ?? this.localPath,
      terminalStatus: terminalStatus,
      requestedParts: requestedParts,
      createdAt: createdAt,
      localState: localState ?? this.localState,
      aiGenerated: aiGenerated,
      aiLabel: aiLabel,
      aiProviderName: aiProviderName,
      aiProviderCode: aiProviderCode,
      aiContentId: aiContentId,
    );
  }

  factory ChatMessage.fromJson(Map<String, dynamic> value) {
    final role = '${value['role'] ?? 'assistant'}';
    final id = '${value['id'] ?? value['event_id'] ?? ''}';
    final aiGenerated = value.containsKey('ai_generated')
        ? value['ai_generated'] == true
        : role == 'assistant';
    return ChatMessage(
      id: id,
      actionId: '${value['action_id'] ?? ''}',
      role: role,
      kind: '${value['kind'] ?? 'text'}',
      text: '${value['text'] ?? ''}',
      mediaId: '${value['media_id'] ?? ''}',
      remoteUrl: '${value['remote_url'] ?? ''}',
      mimeType: '${value['mime_type'] ?? ''}',
      durationMs: asInt(value['duration_ms']),
      streaming: value['streaming'] == true,
      streamToken: '${value['stream_token'] ?? ''}',
      terminalStatus: '${value['terminal_status'] ?? ''}',
      requestedParts: asInt(value['requested_parts']),
      createdAt: asInt(value['created_at']),
      localPath: '${value['local_path'] ?? ''}',
      localState: '${value['local_state'] ?? 'sent'}',
      aiGenerated: aiGenerated,
      aiLabel: '${value['ai_label'] ?? (aiGenerated ? 'AI生成' : '')}',
      aiProviderName:
          '${value['ai_provider_name'] ?? (aiGenerated ? '小悠' : '')}',
      aiProviderCode:
          '${value['ai_provider_code'] ?? (aiGenerated ? 'xiaoyou' : '')}',
      aiContentId: '${value['ai_content_id'] ?? (aiGenerated ? id : '')}',
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'action_id': actionId,
      'role': role,
      'kind': kind,
      'text': text,
      'media_id': mediaId,
      'remote_url': remoteUrl,
      'mime_type': mimeType,
      'duration_ms': durationMs,
      'streaming': streaming,
      'stream_token': streamToken,
      'local_path': localPath,
      'terminal_status': terminalStatus,
      'requested_parts': requestedParts,
      'created_at': createdAt,
      'local_state': localState,
      'ai_generated': aiGenerated,
      'ai_label': aiLabel,
      'ai_provider_name': aiProviderName,
      'ai_provider_code': aiProviderCode,
      'ai_content_id': aiContentId,
    };
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

import 'chat_models.dart';

enum RelationshipEventKind {
  firstLight,
  anniversary,
  photo,
  conversation,
  journal,
  capsule,
  voiceMemory,
  achievement,
}

class RelationshipEntry {
  const RelationshipEntry({
    required this.id,
    required this.kind,
    required this.status,
    required this.title,
    required this.body,
    required this.eventAt,
    required this.unlockAt,
    required this.locked,
  });

  final String id;
  final String kind;
  final String status;
  final String title;
  final Map<String, dynamic> body;
  final int eventAt;
  final int unlockAt;
  final bool locked;

  DateTime get eventTime => DateTime.fromMillisecondsSinceEpoch(eventAt * 1000);

  DateTime? get unlockTime => unlockAt > 0
      ? DateTime.fromMillisecondsSinceEpoch(unlockAt * 1000)
      : null;

  factory RelationshipEntry.fromJson(Map<String, dynamic> value) {
    final rawBody = value['body'];
    return RelationshipEntry(
      id: '${value['entry_id'] ?? ''}',
      kind: '${value['kind'] ?? ''}',
      status: '${value['status'] ?? ''}',
      title: '${value['title'] ?? ''}',
      body: rawBody is Map
          ? rawBody.cast<String, dynamic>()
          : const <String, dynamic>{},
      eventAt: asInt(value['event_at']),
      unlockAt: asInt(value['unlock_at']),
      locked: value['locked'] == true,
    );
  }
}

class RelationshipOrbitEvent {
  const RelationshipOrbitEvent({
    required this.id,
    required this.kind,
    required this.title,
    required this.subtitle,
    required this.occurredAt,
    this.message,
    this.entry,
    this.locked = false,
    this.newlyUnlocked = false,
  });

  final String id;
  final RelationshipEventKind kind;
  final String title;
  final String subtitle;
  final DateTime occurredAt;
  final ChatMessage? message;
  final RelationshipEntry? entry;
  final bool locked;
  final bool newlyUnlocked;
}

class DailyJournal {
  const DailyJournal({
    required this.entry,
    required this.summary,
    required this.moodChanges,
    required this.representativeMediaId,
    required this.quoteSpeaker,
    required this.savedQuote,
    required this.tomorrowWish,
  });

  final RelationshipEntry entry;
  final String summary;
  final List<String> moodChanges;
  final String representativeMediaId;
  final String quoteSpeaker;
  final String savedQuote;
  final String tomorrowWish;

  bool get confirmed => entry.status == 'confirmed';

  factory DailyJournal.fromEntry(RelationshipEntry entry) {
    final quote = entry.body['saved_quote'];
    final moods = entry.body['mood_changes'];
    return DailyJournal(
      entry: entry,
      summary: '${entry.body['summary'] ?? ''}',
      moodChanges: moods is List
          ? moods
              .map((item) => '$item')
              .where((item) => item.isNotEmpty)
              .toList()
          : const [],
      representativeMediaId: '${entry.body['representative_media_id'] ?? ''}',
      quoteSpeaker: quote is Map ? '${quote['speaker'] ?? ''}' : '',
      savedQuote: quote is Map ? '${quote['text'] ?? ''}' : '',
      tomorrowWish: '${entry.body['tomorrow_wish'] ?? ''}',
    );
  }
}

class RelationshipOrbitBuilder {
  const RelationshipOrbitBuilder._();

  static List<RelationshipOrbitEvent> build({
    required List<ChatMessage> messages,
    required List<RelationshipEntry> entries,
    required Set<String> favoriteMessageIds,
  }) {
    final result = <RelationshipOrbitEvent>[];
    if (messages.isNotEmpty) {
      final first = messages.first;
      result.add(
        RelationshipOrbitEvent(
          id: 'first-${first.id}',
          kind: RelationshipEventKind.firstLight,
          title: '故事开始的第一句话',
          subtitle: first.text.isEmpty ? '从这里开始相遇' : first.text,
          occurredAt: first.timestamp,
          message: first,
        ),
      );
      final firstPhoto = _firstOfKind(messages, const {'image', 'sticker'});
      if (firstPhoto != null) {
        result.add(
          RelationshipOrbitEvent(
            id: 'photo-${firstPhoto.id}',
            kind: RelationshipEventKind.photo,
            title: '第一张共同珍藏',
            subtitle: firstPhoto.text.isEmpty ? '一张属于你们的照片' : firstPhoto.text,
            occurredAt: firstPhoto.timestamp,
            message: firstPhoto,
          ),
        );
      }
      final firstVoice = _firstOfKind(messages, const {'voice'});
      if (firstVoice != null) {
        result.add(
          RelationshipOrbitEvent(
            id: 'voice-${firstVoice.id}',
            kind: RelationshipEventKind.voiceMemory,
            title: '第一次听见彼此',
            subtitle: firstVoice.text,
            occurredAt: firstVoice.timestamp,
            message: firstVoice,
          ),
        );
      }
      for (final message in messages.reversed) {
        if (!favoriteMessageIds.contains(message.id)) {
          continue;
        }
        result.add(
          RelationshipOrbitEvent(
            id: 'favorite-${message.id}',
            kind: RelationshipEventKind.conversation,
            title: '被珍藏的一句话',
            subtitle: message.text,
            occurredAt: message.timestamp,
            message: message,
          ),
        );
        if (result
                .where(
                    (event) => event.kind == RelationshipEventKind.conversation)
                .length >=
            2) {
          break;
        }
      }
      final days = messages
          .map(
            (message) => DateTime(
              message.timestamp.year,
              message.timestamp.month,
              message.timestamp.day,
            ),
          )
          .toSet()
          .length;
      if (days >= 3) {
        result.add(
          RelationshipOrbitEvent(
            id: 'days-$days',
            kind: RelationshipEventKind.anniversary,
            title: '相伴的第 $days 天',
            subtitle: '普通日子也已经连成一段故事',
            occurredAt: messages.last.timestamp,
            newlyUnlocked: true,
          ),
        );
      }
    }

    for (final entry in entries) {
      final kind = switch (entry.kind) {
        'journal' => RelationshipEventKind.journal,
        'capsule' => RelationshipEventKind.capsule,
        'voice_memory' => RelationshipEventKind.voiceMemory,
        _ => RelationshipEventKind.achievement,
      };
      final subtitle = switch (entry.kind) {
        'journal' => '${entry.body['summary'] ?? ''}',
        'capsule' => entry.locked ? '会在约定的时间打开' : '${entry.body['text'] ?? ''}',
        'voice_memory' => '一起说了 ${asInt(entry.body['turn_count'])} 个来回',
        _ => '',
      };
      result.add(
        RelationshipOrbitEvent(
          id: entry.id,
          kind: kind,
          title: entry.title,
          subtitle: subtitle,
          occurredAt: entry.eventTime,
          entry: entry,
          locked: entry.locked,
        ),
      );
    }
    result.sort((left, right) => left.occurredAt.compareTo(right.occurredAt));
    return result;
  }

  static ChatMessage? _firstOfKind(
    List<ChatMessage> messages,
    Set<String> kinds,
  ) {
    for (final message in messages) {
      if (kinds.contains(message.kind)) {
        return message;
      }
    }
    return null;
  }
}

import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import 'chat_models.dart';

typedef ArchiveRootProvider = Future<Directory> Function();

class ChatArchiveStore {
  ChatArchiveStore({ArchiveRootProvider? rootProvider})
      : _rootProvider = rootProvider ?? getApplicationSupportDirectory;

  final ArchiveRootProvider _rootProvider;
  Future<void> _writeTail = Future<void>.value();

  Future<List<ChatMessage>> load(String scope) async {
    final file = await _archiveFile(scope);
    if (!await file.exists()) {
      return const [];
    }
    try {
      final payload = jsonDecode(await file.readAsString());
      if (payload is! Map || payload['messages'] is! List) {
        return const [];
      }
      return (payload['messages'] as List)
          .whereType<Map>()
          .map(
            (value) => ChatMessage.fromJson(
              value.cast<String, dynamic>(),
            ),
          )
          .where((message) => message.id.isNotEmpty)
          .toList()
        ..sort(_compareMessages);
    } on FileSystemException {
      return const [];
    } on FormatException {
      return const [];
    }
  }

  Future<void> replace(String scope, Iterable<ChatMessage> messages) {
    final snapshot = mergeChatMessages(
      messages.where(
        (message) => message.id.isNotEmpty && message.localState == 'sent',
      ),
    );
    _writeTail =
        _writeTail.catchError((_) {}).then((_) => _replace(scope, snapshot));
    return _writeTail;
  }

  Future<void> _replace(
    String scope,
    List<ChatMessage> messages,
  ) async {
    final file = await _archiveFile(scope);
    await file.parent.create(recursive: true);
    final temporary = File('${file.path}.tmp');
    await temporary.writeAsString(
      jsonEncode({
        'version': 1,
        'saved_at': DateTime.now().toUtc().toIso8601String(),
        'messages': messages.map((message) => message.toJson()).toList(),
      }),
      flush: true,
    );
    if (await file.exists()) {
      await file.delete();
    }
    await temporary.rename(file.path);
  }

  Future<File> _archiveFile(String scope) async {
    final root = await _rootProvider();
    final encodedScope = base64Url
        .encode(utf8.encode(scope.trim().toLowerCase()))
        .replaceAll('=', '');
    final safeScope = encodedScope.isEmpty ? 'default' : encodedScope;
    return File(
      '${root.path}${Platform.pathSeparator}chat_history'
      '${Platform.pathSeparator}$safeScope.json',
    );
  }
}

List<ChatMessage> mergeChatMessages(Iterable<ChatMessage> messages) {
  final byId = <String, ChatMessage>{};
  for (final message in messages) {
    if (message.id.isNotEmpty) {
      byId[message.id] = message;
    }
  }
  return byId.values.toList()..sort(_compareMessages);
}

int _compareMessages(ChatMessage left, ChatMessage right) {
  final byTime = left.createdAt.compareTo(right.createdAt);
  return byTime != 0 ? byTime : left.id.compareTo(right.id);
}

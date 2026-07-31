import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:path_provider/path_provider.dart';

import 'chat_models.dart';
import 'xiaoyou_api.dart';

typedef MediaCacheRootProvider = Future<Directory> Function();

class MessageMediaCache {
  MessageMediaCache({MediaCacheRootProvider? rootProvider})
      : _rootProvider = rootProvider ?? _defaultRoot;

  final MediaCacheRootProvider _rootProvider;
  final Map<String, Future<File?>> _inFlight = <String, Future<File?>>{};
  Directory? _root;

  static Future<Directory> _defaultRoot() async {
    final support = await getApplicationSupportDirectory();
    return Directory(_join(<String>[support.path, 'chat_media']));
  }

  static bool supports(ChatMessage message) =>
      const {'image', 'sticker', 'voice'}.contains(message.kind);

  Future<File?> resolveMessage(
    ChatMessage message,
    XiaoyouApi? api,
  ) async {
    if (!supports(message)) {
      return null;
    }
    final sourcePath = message.localPath.trim();
    if (sourcePath.isNotEmpty) {
      final source = File(sourcePath);
      if (await source.exists()) {
        if (message.localState != 'sent') {
          return source;
        }
        return cacheLocalFile(message, source);
      }
    }
    if (message.localState != 'sent' || api == null) {
      return existingFor(message);
    }
    if (message.mediaId.trim().isNotEmpty) {
      return ensureCached(
        message,
        () => api.downloadMedia(message.mediaId),
      );
    }
    if (message.remoteUrl.trim().isNotEmpty) {
      return ensureCached(
        message,
        () => api.downloadRemoteMedia(message.remoteUrl),
      );
    }
    return existingFor(message);
  }

  Future<File?> existingFor(ChatMessage message) async {
    if (!supports(message)) {
      return null;
    }
    final target = await targetFileFor(message);
    if (await _nonEmpty(target)) {
      return target;
    }
    return null;
  }

  Future<File?> cacheLocalFile(
    ChatMessage message,
    File source,
  ) async {
    if (!supports(message) || !await _nonEmpty(source)) {
      return null;
    }
    final target = await targetFileFor(message);
    if (_samePath(source.path, target.path)) {
      return source;
    }
    return _deduplicated(
      target.path,
      () async {
        if (await _nonEmpty(target)) {
          return target;
        }
        final bytes = await source.readAsBytes();
        return _writeAtomically(target, bytes);
      },
    );
  }

  Future<File?> ensureCached(
    ChatMessage message,
    Future<MediaPayload> Function() loader,
  ) async {
    if (!supports(message) || message.localState != 'sent') {
      return null;
    }
    final target = await targetFileFor(message);
    return _deduplicated(
      target.path,
      () async {
        if (await _nonEmpty(target)) {
          return target;
        }
        try {
          final payload = await loader();
          if (payload.bytes.isEmpty) {
            return null;
          }
          return _writeAtomically(target, payload.bytes);
        } catch (_) {
          return null;
        }
      },
    );
  }

  Future<File> targetFileFor(ChatMessage message) async {
    final root = await _cacheRoot();
    final localDate = message.timestamp.toLocal();
    final date = _dateKey(localDate);
    final category = message.kind == 'voice' ? 'voice' : 'images';
    final directory = Directory(
      _join(<String>[root.path, date, category]),
    );
    final identity = message.id.trim().isNotEmpty
        ? message.id.trim()
        : '${message.role}-${message.createdAt}-${message.mediaId}';
    final readable = identity
        .replaceAll(RegExp(r'[^A-Za-z0-9_-]+'), '_')
        .replaceAll(RegExp(r'_+'), '_');
    final prefix = readable.isEmpty
        ? 'media'
        : readable.substring(0, readable.length.clamp(0, 44));
    final fileName = '${message.createdAt}_${prefix}_${_stableHash(identity)}'
        '${_extension(message.mimeType, message.kind)}';
    return File(_join(<String>[directory.path, fileName]));
  }

  Future<Directory> _cacheRoot() async {
    final existing = _root;
    if (existing != null) {
      return existing;
    }
    final root = await _rootProvider();
    await root.create(recursive: true);
    _root = root;
    return root;
  }

  Future<File?> _deduplicated(
    String key,
    Future<File?> Function() operation,
  ) {
    final existing = _inFlight[key];
    if (existing != null) {
      return existing;
    }
    final future = operation();
    _inFlight[key] = future;
    return future.whenComplete(() {
      if (identical(_inFlight[key], future)) {
        _inFlight.remove(key);
      }
    });
  }

  Future<File?> _writeAtomically(File target, Uint8List bytes) async {
    if (bytes.isEmpty) {
      return null;
    }
    await target.parent.create(recursive: true);
    final temporary = File(
      '${target.path}.${DateTime.now().microsecondsSinceEpoch}.part',
    );
    try {
      await temporary.writeAsBytes(bytes, flush: true);
      if (!await _nonEmpty(temporary)) {
        return null;
      }
      if (await target.exists()) {
        await target.delete();
      }
      return await temporary.rename(target.path);
    } finally {
      if (await temporary.exists()) {
        await temporary.delete();
      }
    }
  }

  static Future<bool> _nonEmpty(File file) async {
    try {
      return await file.exists() && await file.length() > 0;
    } catch (_) {
      return false;
    }
  }

  static bool _samePath(String left, String right) {
    final normalize = Platform.isWindows
        ? (String value) => value.replaceAll('/', r'\').toLowerCase()
        : (String value) => value;
    return normalize(left) == normalize(right);
  }

  static String _dateKey(DateTime value) =>
      '${value.year.toString().padLeft(4, '0')}-'
      '${value.month.toString().padLeft(2, '0')}-'
      '${value.day.toString().padLeft(2, '0')}';

  static String _extension(String mimeType, String kind) {
    switch (mimeType.trim().toLowerCase()) {
      case 'image/png':
        return '.png';
      case 'image/webp':
        return '.webp';
      case 'image/gif':
        return '.gif';
      case 'image/jpeg':
      case 'image/jpg':
        return '.jpg';
      case 'audio/wav':
      case 'audio/x-wav':
        return '.wav';
      case 'audio/mpeg':
        return '.mp3';
      case 'audio/ogg':
        return '.ogg';
      case 'audio/opus':
        return '.opus';
      case 'audio/webm':
        return '.webm';
      case 'audio/aac':
        return '.aac';
      case 'audio/mp4':
      case 'audio/m4a':
      case 'audio/x-m4a':
        return '.m4a';
      default:
        return kind == 'voice' ? '.audio' : '.image';
    }
  }

  static String _stableHash(String value) {
    var hash = 0xcbf29ce484222325;
    for (final byte in value.codeUnits) {
      hash ^= byte;
      hash = (hash * 0x100000001b3) & 0xffffffffffffffff;
    }
    return hash.toRadixString(16).padLeft(16, '0');
  }

  static String _join(List<String> parts) =>
      parts.where((part) => part.isNotEmpty).join(Platform.pathSeparator);
}

import 'dart:io';

import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

class RecordedVoice {
  const RecordedVoice({
    required this.path,
    required this.mimeType,
    required this.durationMs,
  });

  final String path;
  final String mimeType;
  final int durationMs;
}

class VoiceRecorderController {
  final AudioRecorder _recorder = AudioRecorder();
  DateTime? _startedAt;
  Directory? _temporaryDirectory;
  String? _path;

  Stream<double> amplitudeStream() {
    return _recorder
        .onAmplitudeChanged(const Duration(milliseconds: 90))
        .map((value) => value.current);
  }

  Future<bool> prepare() async {
    final allowed = await _recorder.hasPermission();
    if (!allowed) {
      return false;
    }
    _temporaryDirectory ??= await getTemporaryDirectory();
    return true;
  }

  Future<bool> start() async {
    if (!await prepare()) {
      return false;
    }
    final directory = _temporaryDirectory!;
    final path = '${directory.path}${Platform.pathSeparator}'
        'xiaoyou-voice-${DateTime.now().microsecondsSinceEpoch}.m4a';
    await _recorder.start(
      const RecordConfig(
        encoder: AudioEncoder.aacLc,
        bitRate: 64000,
        sampleRate: 16000,
        numChannels: 1,
      ),
      path: path,
    );
    _path = path;
    _startedAt = DateTime.now();
    return true;
  }

  Future<RecordedVoice?> stop() async {
    final startedAt = _startedAt;
    final recordedPath = await _recorder.stop() ?? _path;
    _startedAt = null;
    _path = null;
    if (recordedPath == null || startedAt == null) {
      return null;
    }
    final file = File(recordedPath);
    if (!await file.exists() || await file.length() < 128) {
      return null;
    }
    return RecordedVoice(
      path: recordedPath,
      mimeType: 'audio/mp4',
      durationMs: DateTime.now().difference(startedAt).inMilliseconds,
    );
  }

  Future<void> cancel() async {
    final path = _path;
    await _recorder.cancel();
    _startedAt = null;
    _path = null;
    if (path != null) {
      final file = File(path);
      if (await file.exists()) {
        await file.delete();
      }
    }
  }

  Future<void> dispose() async {
    await _recorder.dispose();
  }
}

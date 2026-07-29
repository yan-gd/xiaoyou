import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

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
  String _mimeType = 'audio/mp4';
  bool _streaming = false;

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

  Future<bool> start({bool pcm16Wav = false}) async {
    if (!await prepare()) {
      return false;
    }
    final directory = _temporaryDirectory!;
    final extension = pcm16Wav ? 'wav' : 'm4a';
    final path = '${directory.path}${Platform.pathSeparator}'
        'xiaoyou-voice-${DateTime.now().microsecondsSinceEpoch}.$extension';
    await _recorder.start(
      RecordConfig(
        encoder: pcm16Wav ? AudioEncoder.pcm16bits : AudioEncoder.aacLc,
        bitRate: pcm16Wav ? 256000 : 64000,
        sampleRate: 16000,
        numChannels: 1,
      ),
      path: path,
    );
    _path = path;
    _mimeType = pcm16Wav ? 'audio/wav' : 'audio/mp4';
    _startedAt = DateTime.now();
    return true;
  }

  Future<Stream<Uint8List>?> startPcmStream() async {
    if (!await prepare()) {
      return null;
    }
    if (_streaming) {
      await _recorder.stop();
    }
    final stream = await _recorder.startStream(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        bitRate: 256000,
        sampleRate: 16000,
        numChannels: 1,
        autoGain: true,
        echoCancel: true,
        noiseSuppress: true,
        streamBufferSize: 3200,
      ),
    );
    _streaming = true;
    _startedAt = DateTime.now();
    return stream;
  }

  Future<void> stopPcmStream() async {
    if (!_streaming) {
      return;
    }
    await _recorder.stop();
    _streaming = false;
    _startedAt = null;
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
      mimeType: _mimeType,
      durationMs: DateTime.now().difference(startedAt).inMilliseconds,
    );
  }

  Future<void> cancel() async {
    final path = _path;
    await _recorder.cancel();
    _startedAt = null;
    _path = null;
    _mimeType = 'audio/mp4';
    _streaming = false;
    if (path != null) {
      final file = File(path);
      if (await file.exists()) {
        await file.delete();
      }
    }
  }

  Future<void> dispose() async {
    if (_streaming) {
      await _recorder.stop();
      _streaming = false;
    }
    await _recorder.dispose();
  }
}

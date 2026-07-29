import 'package:flutter/services.dart';

/// Low-latency PCM playback used by the Android realtime voice room.
///
/// The native side owns one streaming AudioTrack.  Writing small PCM chunks
/// avoids the gaps caused by creating a media player for every O2.0 packet and
/// lets barge-in flush the speaker immediately.
class RealtimeVoicePlayer {
  static const _channel = MethodChannel(
    'com.yoyo.xiaoyou/realtime_audio',
  );

  bool _started = false;
  final double gain;

  RealtimeVoicePlayer({this.gain = 2.0});

  Future<void> start({int sampleRate = 24000}) async {
    if (_started) {
      return;
    }
    await _channel.invokeMethod<void>(
      'start',
      <String, Object>{
        'sampleRate': sampleRate,
        'gain': gain,
      },
    );
    _started = true;
  }

  Future<void> write(Uint8List pcm) async {
    if (pcm.isEmpty) {
      return;
    }
    await start();
    await _channel.invokeMethod<void>(
      'write',
      <String, Object>{'pcm': pcm},
    );
  }

  Future<int> positionMs() async {
    if (!_started) {
      return 0;
    }
    return await _channel.invokeMethod<int>('positionMs') ?? 0;
  }

  Future<void> stop() async {
    if (!_started) {
      return;
    }
    _started = false;
    await _channel.invokeMethod<void>('stop');
  }

  Future<void> dispose() => stop();
}

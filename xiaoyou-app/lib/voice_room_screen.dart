import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'realtime_voice_player.dart';
import 'voice_recorder.dart';
import 'xiaoyou_api.dart';

const _voiceInk = Color(0xff3d2b36);
const _voiceMuted = Color(0xff9c8792);
const _voiceRose = Color(0xffb35282);
const _voiceLavender = Color(0xff8d71bd);
const _defaultVoiceRoomMoodAsset = 'assets/moods/平静.png';
const _speechMouthMoodAsset = 'assets/moods/无语.png';
// Send 160 ms batches over one dedicated keep-alive connection. The server
// queues and re-times these into O2.0's required 640-byte/20 ms frames without
// holding the public HTTP request open for the duration of the audio.
const _realtimeUploadBytes = 5120;

final Map<String, Future<ui.Image>> _voiceRoomImageCache = {};
Future<ui.FragmentProgram>? _voiceRoomFaceProgram;

Future<ui.Image> _loadVoiceRoomImage(String asset) {
  return _voiceRoomImageCache.putIfAbsent(asset, () async {
    final bytes = await rootBundle.load(asset);
    final codec = await ui.instantiateImageCodec(
      bytes.buffer.asUint8List(),
      targetWidth: 768,
      targetHeight: 768,
    );
    try {
      final frame = await codec.getNextFrame();
      return frame.image;
    } finally {
      codec.dispose();
    }
  });
}

Future<ui.FragmentProgram> _loadVoiceRoomFaceProgram() {
  return _voiceRoomFaceProgram ??= ui.FragmentProgram.fromAsset(
    'shaders/digital_xiaoyou.frag',
  );
}

Future<void> prewarmVoiceRoomVisuals({
  String moodAsset = _defaultVoiceRoomMoodAsset,
}) async {
  try {
    await Future.wait<Object>([
      _loadVoiceRoomFaceProgram(),
      _loadVoiceRoomImage(moodAsset),
      _loadVoiceRoomImage(_speechMouthMoodAsset),
    ]);
  } catch (error) {
    debugPrint('[VoiceRoom] visual prewarm unavailable: $error');
  }
}

enum _RoomPhase {
  connecting,
  listening,
  userSpeaking,
  thinking,
  speaking,
  interrupted,
  ending,
}

class VoiceRoomScreen extends StatefulWidget {
  const VoiceRoomScreen({
    super.key,
    required this.api,
    required this.initialEventSequence,
    this.initialMoodAsset = _defaultVoiceRoomMoodAsset,
    this.initialMoodLabel = '平静',
  });

  final XiaoyouApi api;
  final int initialEventSequence;
  final String initialMoodAsset;
  final String initialMoodLabel;

  @override
  State<VoiceRoomScreen> createState() => _VoiceRoomScreenState();
}

class _VoiceRoomScreenState extends State<VoiceRoomScreen>
    with TickerProviderStateMixin {
  final _recorder = VoiceRecorderController();
  final _player = RealtimeVoicePlayer(gain: 2.0);
  final _level = ValueNotifier<double>(0.08);
  final _audioQueue = Queue<Uint8List>();
  int _queuedAudioBytes = 0;
  StreamSubscription<Uint8List>? _pcmSubscription;
  StreamSubscription<double>? _amplitude;
  Timer? _clock;
  late final AnimationController _breath = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2600),
  )..repeat(reverse: true);
  late final AnimationController _particles = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 12),
  )..repeat();

  _RoomPhase _phase = _RoomPhase.connecting;
  int _elapsedSeconds = 0;
  int _liveSequence = 0;
  String _roomId = '';
  String _currentReplyId = '';
  String _playbackReplyId = '';
  String _suppressedReplyId = '';
  int _replyPlaybackBaseMs = 0;
  int _replyAudioDurationMs = 0;
  Timer? _playbackDrain;
  Timer? _mouthDecay;
  String _moodAsset = _defaultVoiceRoomMoodAsset;
  String _moodLabel = '平静';
  String _liveCaption = '';
  bool _muted = false;
  bool _sendingAudio = false;
  int _audioUploadFailures = 0;
  bool _initializing = true;
  bool _recovering = false;
  bool _closed = false;

  @override
  void initState() {
    super.initState();
    _moodAsset = widget.initialMoodAsset;
    _moodLabel = widget.initialMoodLabel;
    _clock = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) {
        setState(() => _elapsedSeconds += 1);
      }
    });
    unawaited(prewarmVoiceRoomVisuals(moodAsset: _moodAsset));
    unawaited(_recorder.prepare());
    unawaited(_initializeRoom());
  }

  @override
  void dispose() {
    _closed = true;
    _clock?.cancel();
    _playbackDrain?.cancel();
    _mouthDecay?.cancel();
    _pcmSubscription?.cancel();
    _amplitude?.cancel();
    _breath.dispose();
    _particles.dispose();
    _level.dispose();
    unawaited(_player.dispose());
    unawaited(_recorder.dispose());
    super.dispose();
  }

  Future<void> _initializeRoom() async {
    try {
      final room = await widget.api.createVoiceRoom();
      if (!mounted || _closed) {
        if (room.roomId.isNotEmpty) {
          unawaited(widget.api.finishVoiceRoom(room.roomId));
        }
        return;
      }
      setState(() {
        _roomId = room.roomId;
        _initializing = false;
        _phase = _RoomPhase.listening;
      });
      unawaited(_refreshMood());
      await _startRealtimeMicrophone();
      unawaited(_pollLiveEvents());
    } catch (_) {
      if (!mounted) {
        return;
      }
      setState(() => _initializing = false);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('语音房暂时没有连上，请检查火山 O2.0 配置后重试'),
        ),
      );
    }
  }

  Future<void> _recoverRoom() async {
    if (_recovering || _closed || _phase == _RoomPhase.ending) {
      return;
    }
    _recovering = true;
    final previousRoomId = _roomId;
    _audioQueue.clear();
    _queuedAudioBytes = 0;
    _playbackDrain?.cancel();
    await _player.stop();
    _playbackReplyId = '';
    _replyPlaybackBaseMs = 0;
    _replyAudioDurationMs = 0;
    _suppressedReplyId = '';
    if (mounted) {
      setState(() {
        _phase = _RoomPhase.connecting;
        _liveCaption = '正在重新连上小悠…';
      });
    }

    if (previousRoomId.isNotEmpty) {
      try {
        await widget.api.finishVoiceRoom(previousRoomId);
      } catch (_) {
        // A broken provider socket must not prevent creation of its successor.
      }
    }

    Object? lastError;
    for (var attempt = 0; attempt < 3 && !_closed; attempt += 1) {
      try {
        final room = await widget.api.createVoiceRoom();
        if (_closed || !mounted) {
          if (room.roomId.isNotEmpty) {
            unawaited(widget.api.finishVoiceRoom(room.roomId));
          }
          _recovering = false;
          return;
        }
        setState(() {
          _roomId = room.roomId;
          _liveSequence = 0;
          _audioUploadFailures = 0;
          _recovering = false;
          _phase = _RoomPhase.listening;
          _liveCaption = '重新连上了，继续说吧';
        });
        return;
      } catch (error) {
        lastError = error;
        if (attempt < 2) {
          await Future<void>.delayed(
            Duration(milliseconds: 420 * (attempt + 1)),
          );
        }
      }
    }

    debugPrint('[VoiceRoom] reconnect failed: $lastError');
    _recovering = false;
    if (mounted && !_closed) {
      setState(() {
        _phase = _RoomPhase.connecting;
        _liveCaption = '暂时没能重新连上，请结束后再试一次';
      });
    }
  }

  Future<void> _refreshMood() async {
    try {
      final profile = await widget.api.profile();
      final mood = profile['mood'];
      if (mood is! Map || !mounted) {
        return;
      }
      const assets = {
        '平静.png': 'assets/moods/平静.png',
        '开心.png': 'assets/moods/开心.png',
        '惊讶.png': 'assets/moods/惊讶.png',
        '愤怒.png': 'assets/moods/愤怒.png',
        '无语.png': 'assets/moods/无语.png',
        '难过.png': 'assets/moods/难过.png',
        '害羞.png': 'assets/moods/害羞.png',
        '大哭.png': 'assets/moods/大哭.png',
        '害怕.png': 'assets/moods/害怕.png',
      };
      final asset = assets['${mood['asset'] ?? ''}'];
      if (asset == null) {
        return;
      }
      setState(() {
        _moodAsset = asset;
        _moodLabel = '${mood['label'] ?? '平静'}';
      });
    } catch (_) {
      // The call remains usable when the decorative mood profile is offline.
    }
  }

  Future<void> _startRealtimeMicrophone() async {
    final stream = await _recorder.startPcmStream();
    if (stream == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('需要麦克风权限才能进入语音房间')),
        );
      }
      return;
    }
    await _pcmSubscription?.cancel();
    _pcmSubscription = stream.listen(
      (pcm) {
        if (_closed || _recovering || pcm.isEmpty) {
          return;
        }
        // O2.0 keep_alive keeps the session open while muted.  It explicitly
        // does not require fabricated silence, so muted microphone data is
        // discarded instead of being uploaded as zero-filled PCM.
        if (_muted) {
          return;
        }
        final chunk = Uint8List.fromList(pcm);
        if (_audioQueue.length >= 24) {
          _queuedAudioBytes -= _audioQueue.removeFirst().length;
        }
        _audioQueue.add(chunk);
        _queuedAudioBytes += chunk.length;
        if (_queuedAudioBytes >= _realtimeUploadBytes) {
          unawaited(_pumpAudioQueue());
        }
      },
      onError: (_) {
        if (mounted && !_closed) {
          setState(() => _liveCaption = '麦克风连接正在恢复…');
        }
      },
    );
    await _amplitude?.cancel();
    _amplitude = _recorder.amplitudeStream().listen((decibels) {
      if (!mounted || _muted || _phase == _RoomPhase.speaking) {
        return;
      }
      final next = ((decibels + 55) / 55).clamp(0.06, 1.0);
      _level.value = _level.value * 0.58 + next * 0.42;
    });
  }

  Future<void> _pumpAudioQueue() async {
    if (_sendingAudio || _recovering || _roomId.isEmpty || _closed) {
      return;
    }
    _sendingAudio = true;
    Uint8List? inFlightPacket;
    String inFlightRoomId = '';
    try {
      while (_queuedAudioBytes >= _realtimeUploadBytes &&
          !_closed &&
          !_recovering) {
        final targetRoomId = _roomId;
        inFlightRoomId = targetRoomId;
        final packet = BytesBuilder(copy: false);
        while (_audioQueue.isNotEmpty && packet.length < _realtimeUploadBytes) {
          final chunk = _audioQueue.removeFirst();
          _queuedAudioBytes -= chunk.length;
          final remaining = _realtimeUploadBytes - packet.length;
          if (chunk.length <= remaining) {
            packet.add(chunk);
          } else {
            packet.add(Uint8List.sublistView(chunk, 0, remaining));
            final remainder = Uint8List.sublistView(chunk, remaining);
            _audioQueue.addFirst(remainder);
            _queuedAudioBytes += remainder.length;
          }
        }
        inFlightPacket = packet.takeBytes();
        await widget.api.sendVoiceRoomAudio(
          roomId: targetRoomId,
          pcm: inFlightPacket,
        );
        if (targetRoomId != _roomId) {
          inFlightPacket = null;
          continue;
        }
        inFlightPacket = null;
        _audioUploadFailures = 0;
      }
    } catch (error) {
      debugPrint('[VoiceRoom] realtime audio upload failed: $error');
      _audioUploadFailures += 1;
      final failedPacket = inFlightPacket;
      if (failedPacket != null &&
          failedPacket.isNotEmpty &&
          !_recovering &&
          inFlightRoomId == _roomId) {
        _audioQueue.addFirst(failedPacket);
        _queuedAudioBytes += failedPacket.length;
      }
      if (_audioQueue.length > 8) {
        while (_audioQueue.length > 8) {
          _queuedAudioBytes -= _audioQueue.removeFirst().length;
        }
      }
      if (mounted && !_closed) {
        setState(() {
          _liveCaption =
              _audioUploadFailures >= 3 ? '声音还没送到小悠，正在重新连接…' : '声音连接晃了一下，正在恢复…';
        });
      }
      if (_audioUploadFailures >= 3) {
        unawaited(_recoverRoom());
      }
      await Future<void>.delayed(const Duration(milliseconds: 260));
    } finally {
      _sendingAudio = false;
      if (_queuedAudioBytes >= _realtimeUploadBytes &&
          !_closed &&
          !_recovering) {
        unawaited(_pumpAudioQueue());
      }
    }
  }

  Future<void> _pollLiveEvents() async {
    while (!_closed && _roomId.isNotEmpty) {
      try {
        final targetRoomId = _roomId;
        final targetSequence = _liveSequence;
        final events = await widget.api.voiceRoomEvents(
          roomId: targetRoomId,
          after: targetSequence,
        );
        if (targetRoomId != _roomId) {
          continue;
        }
        for (final event in events) {
          if (targetRoomId != _roomId) {
            break;
          }
          _liveSequence = max(_liveSequence, event.sequence);
          await _handleLiveEvent(event);
        }
      } catch (_) {
        if (!_closed) {
          await Future<void>.delayed(const Duration(milliseconds: 450));
        }
      }
    }
  }

  void _finishPlaybackAfterDrain(String replyId) {
    if (_closed ||
        _playbackReplyId != replyId ||
        _phase == _RoomPhase.userSpeaking) {
      return;
    }
    _playbackReplyId = '';
    _replyPlaybackBaseMs = 0;
    _replyAudioDurationMs = 0;
    _level.value = 0.08;
    if (mounted) {
      setState(() => _phase = _RoomPhase.listening);
    }
  }

  Future<void> _schedulePlaybackDrain(String replyId) async {
    _playbackDrain?.cancel();
    if (replyId.isEmpty || replyId != _playbackReplyId) {
      if (mounted && _phase != _RoomPhase.userSpeaking) {
        setState(() => _phase = _RoomPhase.listening);
        _level.value = 0.08;
      }
      return;
    }
    final absolutePositionMs = await _player.positionMs();
    final playedMs = max(0, absolutePositionMs - _replyPlaybackBaseMs);
    final remainingMs = max(0, _replyAudioDurationMs - playedMs);
    if (remainingMs <= 45) {
      _finishPlaybackAfterDrain(replyId);
      return;
    }
    _playbackDrain = Timer(
      Duration(milliseconds: remainingMs + 55),
      () => _finishPlaybackAfterDrain(replyId),
    );
  }

  Future<void> _handleLiveEvent(VoiceRoomLiveEvent event) async {
    if (_closed || !mounted) {
      return;
    }
    final payload = event.payload;
    switch (event.type) {
      case 'listening':
        setState(() => _phase = _RoomPhase.listening);
        return;
      case 'user_speech_started':
        final interrupted = payload['interrupted'] == true;
        final playbackPositionMs = await _player.positionMs();
        final playedMs = max(0, playbackPositionMs - _replyPlaybackBaseMs);
        final playbackStillActive = _playbackReplyId.isNotEmpty &&
            playedMs + 80 < _replyAudioDurationMs;
        if (interrupted ||
            _phase == _RoomPhase.speaking ||
            playbackStillActive) {
          final replyId = _playbackReplyId.isNotEmpty
              ? _playbackReplyId
              : '${payload['reply_id'] ?? _currentReplyId}'.trim();
          _playbackDrain?.cancel();
          _suppressedReplyId = replyId;
          await _player.stop();
          _playbackReplyId = '';
          _replyPlaybackBaseMs = 0;
          _replyAudioDurationMs = 0;
          if (replyId.isNotEmpty) {
            unawaited(
              widget.api.truncateVoiceRoomReply(
                roomId: _roomId,
                replyId: replyId,
                audioEndMs: playedMs,
              ),
            );
          }
          setState(() => _phase = _RoomPhase.interrupted);
        }
        if (mounted) {
          setState(() {
            _phase = _RoomPhase.userSpeaking;
            _liveCaption = '我在听';
          });
        }
        return;
      case 'user_transcript':
        final text = '${payload['text'] ?? ''}'.trim();
        if (text.isNotEmpty) {
          setState(() {
            _liveCaption = text;
            if (payload['final'] == true) {
              _phase = _RoomPhase.thinking;
            }
          });
        }
        return;
      case 'thinking':
        setState(() {
          _phase = _RoomPhase.thinking;
          _liveCaption = '让我想想…';
        });
        return;
      case 'assistant_transcript':
        final text = '${payload['text'] ?? ''}'.trim();
        if (text.isNotEmpty) {
          setState(() => _liveCaption = text);
        }
        final replyId = '${payload['reply_id'] ?? ''}'.trim();
        if (replyId.isNotEmpty) {
          if (_suppressedReplyId.isNotEmpty && replyId != _suppressedReplyId) {
            _suppressedReplyId = '';
          }
          _currentReplyId = replyId;
        }
        return;
      case 'assistant_audio':
        final encoded = '${payload['audio'] ?? ''}';
        if (encoded.isEmpty) {
          return;
        }
        final replyId = '${payload['reply_id'] ?? ''}'.trim();
        if (_suppressedReplyId.isNotEmpty &&
            (replyId.isEmpty || replyId == _suppressedReplyId)) {
          // Frames already queued before CLIENT_INTERRUPT can arrive after
          // the local player has stopped. Never let those stale frames restart
          // the interrupted answer.
          return;
        }
        if (replyId.isNotEmpty) {
          if (_suppressedReplyId.isNotEmpty && replyId != _suppressedReplyId) {
            _suppressedReplyId = '';
          }
          _currentReplyId = replyId;
          if (_playbackReplyId != replyId) {
            _playbackDrain?.cancel();
            _replyPlaybackBaseMs = await _player.positionMs();
            _playbackReplyId = replyId;
            _replyAudioDurationMs = 0;
          }
        }
        final decoded = base64Decode(encoded);
        _replyAudioDurationMs += decoded.length * 1000 ~/ (24000 * 2);
        await _player.write(decoded);
        final speechLevel = _pcmSpeechLevel(decoded);
        _level.value =
            (_level.value * 0.64 + speechLevel * 0.36).clamp(0.08, 1.0);
        _mouthDecay?.cancel();
        _mouthDecay = Timer(const Duration(milliseconds: 170), () {
          if (!_closed && _phase == _RoomPhase.speaking) {
            _level.value = max(0.10, _level.value * 0.70);
          }
        });
        if (mounted && _phase != _RoomPhase.speaking) {
          setState(() => _phase = _RoomPhase.speaking);
        }
        return;
      case 'assistant_audio_ended':
        // Keep the native AudioTrack alive. The server's terminal event can
        // arrive while the final PCM frames are still buffered; stopping here
        // used to flush that tail and recreate the track for every reply,
        // producing audible gaps and visible UI stalls.
        final endedReplyId =
            '${payload['reply_id'] ?? _playbackReplyId}'.trim();
        if (_suppressedReplyId.isNotEmpty &&
            endedReplyId == _suppressedReplyId) {
          return;
        }
        await _schedulePlaybackDrain(endedReplyId);
        return;
      case 'interrupted':
        final interruptedReplyId =
            '${payload['reply_id'] ?? _currentReplyId}'.trim();
        if (interruptedReplyId.isNotEmpty) {
          _suppressedReplyId = interruptedReplyId;
        }
        _playbackDrain?.cancel();
        await _player.stop();
        _playbackReplyId = '';
        _replyPlaybackBaseMs = 0;
        _replyAudioDurationMs = 0;
        if (mounted && _phase != _RoomPhase.userSpeaking) {
          setState(() => _phase = _RoomPhase.interrupted);
        }
        return;
      case 'turn_complete':
        unawaited(_refreshMood());
        return;
      case 'error':
        await _recoverRoom();
        return;
    }
  }

  void _toggleMute() {
    HapticFeedback.selectionClick();
    setState(() {
      _muted = !_muted;
      if (_muted) {
        _audioQueue.clear();
        _queuedAudioBytes = 0;
        _level.value = 0.04;
      }
    });
  }

  Future<void> _endRoom() async {
    if (_phase == _RoomPhase.ending) {
      return;
    }
    setState(() => _phase = _RoomPhase.ending);
    _playbackDrain?.cancel();
    await _pcmSubscription?.cancel();
    _pcmSubscription = null;
    await _amplitude?.cancel();
    _amplitude = null;
    await _recorder.stopPcmStream();
    await _player.stop();
    if (_roomId.isNotEmpty) {
      try {
        await widget.api.finishVoiceRoom(_roomId);
      } catch (_) {
        // The persisted turns remain available even if finalization is retried
        // when the next voice-room request reaches the server.
      }
    }
    if (mounted) {
      Navigator.of(context).pop(true);
    }
  }

  Future<void> _showRoomArchive() async {
    HapticFeedback.selectionClick();
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => _VoiceRoomArchiveSheet(api: widget.api),
    );
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) {
          unawaited(_endRoom());
        }
      },
      child: Scaffold(
        body: Stack(
          children: [
            Positioned.fill(
              child: AnimatedBuilder(
                animation: _particles,
                builder: (_, __) => CustomPaint(
                  painter: _VoiceRoomBackgroundPainter(_particles.value),
                ),
              ),
            ),
            SafeArea(
              child: Column(
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(18, 10, 18, 4),
                    child: Row(
                      children: [
                        IconButton.filledTonal(
                          onPressed: _endRoom,
                          icon: const Icon(Icons.keyboard_arrow_down_rounded),
                        ),
                        const Expanded(
                          child: Column(
                            children: [
                              Text(
                                '耳边的一会儿',
                                style: TextStyle(
                                  color: _voiceInk,
                                  fontSize: 19,
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                              Text(
                                '随时开口，也可以随时打断',
                                style: TextStyle(
                                  color: _voiceMuted,
                                  fontSize: 11,
                                ),
                              ),
                            ],
                          ),
                        ),
                        IconButton(
                          tooltip: '语音房记录',
                          onPressed: _showRoomArchive,
                          icon: const Icon(Icons.auto_stories_rounded),
                          color: _voiceRose,
                        ),
                        SizedBox(
                          width: 48,
                          child: Text(
                            _duration(_elapsedSeconds),
                            textAlign: TextAlign.end,
                            style: const TextStyle(
                              color: _voiceMuted,
                              fontFeatures: [FontFeature.tabularFigures()],
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  Expanded(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        AnimatedBuilder(
                          animation: Listenable.merge([
                            _breath,
                            _particles,
                            _level,
                          ]),
                          builder: (_, __) => _VoiceOrb(
                            phase:
                                _initializing ? _RoomPhase.connecting : _phase,
                            level: _level.value,
                            progress: _particles.value,
                            breath: Curves.easeInOutSine.transform(
                              _breath.value,
                            ),
                            moodAsset: _moodAsset,
                          ),
                        ),
                        const SizedBox(height: 24),
                        AnimatedSwitcher(
                          duration: const Duration(milliseconds: 360),
                          transitionBuilder: (child, animation) =>
                              FadeTransition(
                            opacity: animation,
                            child: SlideTransition(
                              position: Tween<Offset>(
                                begin: const Offset(0, 0.12),
                                end: Offset.zero,
                              ).animate(animation),
                              child: child,
                            ),
                          ),
                          child: Text(
                            _initializing ? '正在连接小悠…' : _phaseLabel(_phase),
                            key: ValueKey('$_phase-$_initializing'),
                            style: const TextStyle(
                              color: _voiceInk,
                              fontSize: 20,
                              fontWeight: FontWeight.w800,
                              letterSpacing: 0.2,
                            ),
                          ),
                        ),
                        const SizedBox(height: 8),
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 34),
                          child: AnimatedSwitcher(
                            duration: const Duration(milliseconds: 260),
                            child: Text(
                              _muted
                                  ? '麦克风已静音'
                                  : (_liveCaption.isEmpty
                                      ? '直接开口即可，我会一直听着'
                                      : _liveCaption),
                              key: ValueKey('$_muted-$_liveCaption'),
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              textAlign: TextAlign.center,
                              style: const TextStyle(
                                color: _voiceMuted,
                                fontSize: 12,
                                height: 1.45,
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(height: 10),
                        Text(
                          '此刻 · $_moodLabel',
                          style: const TextStyle(
                            color: Color(0xffb06c91),
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(22, 6, 22, 22),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        _VoiceControlButton(
                          icon: _muted
                              ? Icons.mic_off_rounded
                              : Icons.mic_rounded,
                          label: _muted ? '取消静音' : '静音',
                          onTap: _toggleMute,
                          dark: false,
                        ),
                        const SizedBox(width: 26),
                        _VoiceControlButton(
                          icon: Icons.close_rounded,
                          label: '结束',
                          onTap: _endRoom,
                          dark: true,
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _VoiceControlButton extends StatelessWidget {
  const _VoiceControlButton({
    required this.icon,
    required this.label,
    required this.onTap,
    required this.dark,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool dark;

  @override
  Widget build(BuildContext context) {
    final background = dark ? const Color(0xff241c21) : Colors.white;
    final foreground = dark ? Colors.white : _voiceInk;
    return Semantics(
      button: true,
      label: label,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Material(
            color: background,
            shape: const CircleBorder(),
            elevation: dark ? 8 : 3,
            shadowColor: const Color(0x30331a27),
            child: InkWell(
              customBorder: const CircleBorder(),
              onTap: onTap,
              child: SizedBox.square(
                dimension: 60,
                child: Icon(icon, color: foreground, size: 25),
              ),
            ),
          ),
          const SizedBox(height: 7),
          Text(
            label,
            style: const TextStyle(
              color: _voiceMuted,
              fontSize: 11,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

class _VoiceRoomArchiveSheet extends StatefulWidget {
  const _VoiceRoomArchiveSheet({required this.api});

  final XiaoyouApi api;

  @override
  State<_VoiceRoomArchiveSheet> createState() => _VoiceRoomArchiveSheetState();
}

class _VoiceRoomArchiveSheetState extends State<_VoiceRoomArchiveSheet> {
  late Future<List<VoiceRoomRecord>> _rooms = widget.api.voiceRooms();

  Future<void> _openRoom(VoiceRoomRecord summary) async {
    try {
      final room = await widget.api.voiceRoom(summary.roomId);
      if (!mounted) {
        return;
      }
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        backgroundColor: Colors.transparent,
        builder: (context) => _VoiceRoomTranscript(room: room),
      );
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('这段语音房记录暂时打不开')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final height = MediaQuery.sizeOf(context).height * 0.74;
    return Container(
      height: height,
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xfffffbfd), Color(0xfffff1f7), Color(0xfff2edff)],
        ),
        borderRadius: BorderRadius.vertical(top: Radius.circular(34)),
      ),
      child: Column(
        children: [
          const SizedBox(height: 10),
          Container(
            width: 44,
            height: 5,
            decoration: BoxDecoration(
              color: const Color(0x33a06b85),
              borderRadius: BorderRadius.circular(99),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(22, 18, 14, 8),
            child: Row(
              children: [
                const Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '耳边收藏',
                        style: TextStyle(
                          color: _voiceInk,
                          fontSize: 23,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      SizedBox(height: 3),
                      Text(
                        '每次相遇各自保存，主聊天页不会混入逐句记录',
                        style: TextStyle(color: _voiceMuted, fontSize: 12),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  tooltip: '刷新',
                  onPressed: () {
                    setState(() => _rooms = widget.api.voiceRooms());
                  },
                  icon: const Icon(Icons.refresh_rounded),
                  color: _voiceRose,
                ),
              ],
            ),
          ),
          Expanded(
            child: FutureBuilder<List<VoiceRoomRecord>>(
              future: _rooms,
              builder: (context, snapshot) {
                if (snapshot.connectionState == ConnectionState.waiting) {
                  return const Center(child: CircularProgressIndicator());
                }
                if (snapshot.hasError) {
                  return const Center(
                    child: Text(
                      '记录暂时没有加载出来',
                      style: TextStyle(color: _voiceMuted),
                    ),
                  );
                }
                final rooms = snapshot.data ?? const [];
                if (rooms.isEmpty) {
                  return const Center(
                    child: Text(
                      '下一次耳边相遇，会收藏在这里',
                      style: TextStyle(color: _voiceMuted),
                    ),
                  );
                }
                return ListView.separated(
                  padding: const EdgeInsets.fromLTRB(18, 8, 18, 28),
                  itemCount: rooms.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 10),
                  itemBuilder: (context, index) {
                    final room = rooms[index];
                    return Material(
                      color: const Color(0xd9ffffff),
                      borderRadius: BorderRadius.circular(24),
                      child: InkWell(
                        borderRadius: BorderRadius.circular(24),
                        onTap: () => _openRoom(room),
                        child: Padding(
                          padding: const EdgeInsets.all(16),
                          child: Row(
                            children: [
                              Container(
                                width: 48,
                                height: 48,
                                decoration: BoxDecoration(
                                  gradient: const LinearGradient(
                                    colors: [
                                      Color(0xffffd9e9),
                                      Color(0xffded4ff),
                                    ],
                                  ),
                                  borderRadius: BorderRadius.circular(17),
                                ),
                                child: const Icon(
                                  Icons.graphic_eq_rounded,
                                  color: _voiceRose,
                                ),
                              ),
                              const SizedBox(width: 13),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      room.title,
                                      style: const TextStyle(
                                        color: _voiceInk,
                                        fontSize: 16,
                                        fontWeight: FontWeight.w700,
                                      ),
                                    ),
                                    const SizedBox(height: 4),
                                    Text(
                                      '${_roomDate(room.startedAt)} · '
                                      '${room.turnCount} 轮对话',
                                      style: const TextStyle(
                                        color: _voiceMuted,
                                        fontSize: 12,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              const Icon(
                                Icons.chevron_right_rounded,
                                color: _voiceMuted,
                              ),
                            ],
                          ),
                        ),
                      ),
                    );
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _VoiceRoomTranscript extends StatelessWidget {
  const _VoiceRoomTranscript({required this.room});

  final VoiceRoomRecord room;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: MediaQuery.sizeOf(context).height * 0.82,
      decoration: const BoxDecoration(
        color: Color(0xfffffbfd),
        borderRadius: BorderRadius.vertical(top: Radius.circular(32)),
      ),
      child: Column(
        children: [
          const SizedBox(height: 10),
          Container(
            width: 44,
            height: 5,
            decoration: BoxDecoration(
              color: const Color(0x33a06b85),
              borderRadius: BorderRadius.circular(99),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(22, 18, 22, 12),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        room.title,
                        style: const TextStyle(
                          color: _voiceInk,
                          fontSize: 22,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      Text(
                        '${_roomDate(room.startedAt)} · '
                        '${room.turnCount} 轮',
                        style: const TextStyle(
                          color: _voiceMuted,
                          fontSize: 12,
                        ),
                      ),
                    ],
                  ),
                ),
                IconButton.filledTonal(
                  onPressed: () => Navigator.of(context).pop(),
                  icon: const Icon(Icons.close_rounded),
                ),
              ],
            ),
          ),
          Expanded(
            child: room.turns.isEmpty
                ? const Center(
                    child: Text(
                      '这一会儿安静地结束了',
                      style: TextStyle(color: _voiceMuted),
                    ),
                  )
                : ListView.builder(
                    padding: const EdgeInsets.fromLTRB(18, 4, 18, 30),
                    itemCount: room.turns.length,
                    itemBuilder: (context, index) {
                      final turn = room.turns[index];
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 18),
                        child: Column(
                          children: [
                            _TranscriptBubble(
                              mine: true,
                              label: 'YoYo',
                              text: turn.userText,
                            ),
                            if (turn.assistantText.isNotEmpty) ...[
                              const SizedBox(height: 8),
                              _TranscriptBubble(
                                mine: false,
                                label:
                                    turn.deliveryComplete ? '小悠' : '小悠 · 被打断',
                                text: turn.assistantText,
                              ),
                            ] else if (!turn.deliveryComplete) ...[
                              const SizedBox(height: 8),
                              const _InterruptedTurnNote(),
                            ],
                          ],
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

class _InterruptedTurnNote extends StatelessWidget {
  const _InterruptedTurnNote();

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
        decoration: BoxDecoration(
          color: const Color(0x80ffffff),
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: const Color(0x1fb35282)),
        ),
        child: const Text(
          '这句回复被你自然地接了过去',
          style: TextStyle(
            color: _voiceMuted,
            fontSize: 12,
            fontStyle: FontStyle.italic,
          ),
        ),
      ),
    );
  }
}

class _TranscriptBubble extends StatelessWidget {
  const _TranscriptBubble({
    required this.mine,
    required this.label,
    required this.text,
  });

  final bool mine;
  final String label;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 310),
        padding: const EdgeInsets.fromLTRB(15, 11, 15, 12),
        decoration: BoxDecoration(
          color: mine ? const Color(0xfff0e6fa) : Colors.white,
          borderRadius: BorderRadius.circular(20),
          boxShadow: const [
            BoxShadow(
              color: Color(0x100e0610),
              blurRadius: 18,
              offset: Offset(0, 7),
            ),
          ],
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              label,
              style: TextStyle(
                color: mine ? _voiceLavender : _voiceRose,
                fontSize: 11,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: 3),
            Text(
              text,
              style: const TextStyle(
                color: _voiceInk,
                height: 1.42,
                fontSize: 15,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

String _roomDate(DateTime value) => '${value.month.toString().padLeft(2, '0')}-'
    '${value.day.toString().padLeft(2, '0')} '
    '${value.hour.toString().padLeft(2, '0')}:'
    '${value.minute.toString().padLeft(2, '0')}';

double _pcmSpeechLevel(Uint8List pcm) {
  if (pcm.length < 2) {
    return 0.08;
  }
  var energy = 0.0;
  var samples = 0;
  for (var index = 0; index + 1 < pcm.length; index += 4) {
    final raw = pcm[index] | (pcm[index + 1] << 8);
    final signed = raw >= 0x8000 ? raw - 0x10000 : raw;
    final normalized = signed / 32768.0;
    energy += normalized * normalized;
    samples += 1;
  }
  if (samples == 0) {
    return 0.08;
  }
  final rms = sqrt(energy / samples);
  return ((rms - 0.006) * 9.5).clamp(0.08, 1.0);
}

class _VoiceOrb extends StatefulWidget {
  const _VoiceOrb({
    required this.phase,
    required this.level,
    required this.progress,
    required this.breath,
    required this.moodAsset,
  });

  final _RoomPhase phase;
  final double level;
  final double progress;
  final double breath;
  final String moodAsset;

  @override
  State<_VoiceOrb> createState() => _VoiceOrbState();
}

class _VoiceOrbState extends State<_VoiceOrb>
    with SingleTickerProviderStateMixin {
  final _random = Random();
  late final AnimationController _blink = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 118),
  );
  Timer? _nextBlink;
  ui.FragmentShader? _faceShader;
  ui.Image? _faceImage;
  ui.Image? _speechMouthImage;
  int _imageLoadGeneration = 0;

  double get _diameter => switch (widget.phase) {
        _RoomPhase.connecting => 214,
        _RoomPhase.listening => 248,
        _RoomPhase.userSpeaking => 286,
        _RoomPhase.thinking => 228,
        _RoomPhase.speaking => 294,
        _RoomPhase.interrupted => 212,
        _RoomPhase.ending => 42,
      };

  double get _activity => switch (widget.phase) {
        _RoomPhase.connecting => 0.18,
        _RoomPhase.listening => 0.30,
        _RoomPhase.userSpeaking => 0.82,
        _RoomPhase.thinking => 0.58,
        _RoomPhase.speaking => 1.0,
        _RoomPhase.interrupted => 0.48,
        _RoomPhase.ending => 0.08,
      };

  double get _headTilt {
    final wave = sin(widget.progress * pi * 2);
    return switch (widget.phase) {
      _RoomPhase.connecting => 0,
      _RoomPhase.listening => wave * 0.016,
      _RoomPhase.userSpeaking => -0.035 + wave * 0.012,
      _RoomPhase.thinking => 0.064 + wave * 0.018,
      _RoomPhase.speaking => wave * (0.020 + widget.level.clamp(0, 1) * 0.020),
      _RoomPhase.interrupted => -0.052,
      _RoomPhase.ending => 0,
    };
  }

  double get _mouthLevel {
    if (widget.phase != _RoomPhase.speaking) {
      return 0;
    }
    final normalized = ((widget.level - 0.08) / 0.92).clamp(0.0, 1.0);
    return Curves.easeOutCubic.transform(normalized);
  }

  @override
  void initState() {
    super.initState();
    unawaited(_loadVisualBundle(widget.moodAsset));
    _scheduleBlink();
  }

  @override
  void didUpdateWidget(covariant _VoiceOrb oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.moodAsset != widget.moodAsset) {
      unawaited(_loadFaceImage(widget.moodAsset));
    }
  }

  @override
  void dispose() {
    _nextBlink?.cancel();
    _blink.dispose();
    super.dispose();
  }

  Future<void> _loadVisualBundle(String asset) async {
    final generation = ++_imageLoadGeneration;
    try {
      final programFuture = _loadVoiceRoomFaceProgram();
      final faceFuture = _loadVoiceRoomImage(asset);
      final mouthFuture = _loadVoiceRoomImage(_speechMouthMoodAsset);
      await Future.wait<Object>([
        programFuture,
        faceFuture,
        mouthFuture,
      ]);
      if (!mounted || generation != _imageLoadGeneration) {
        return;
      }
      final program = await programFuture;
      final face = await faceFuture;
      final mouth = await mouthFuture;
      if (!mounted || generation != _imageLoadGeneration) {
        return;
      }
      setState(() {
        _faceShader = program.fragmentShader();
        _faceImage = face;
        _speechMouthImage = mouth;
      });
    } catch (error) {
      debugPrint('[VoiceRoom] digital avatar visuals unavailable: $error');
      if (mounted && generation == _imageLoadGeneration) {
        unawaited(_loadFaceImage(asset));
      }
    }
  }

  Future<void> _loadFaceImage(String asset) async {
    final generation = ++_imageLoadGeneration;
    try {
      final image = await _loadVoiceRoomImage(asset);
      if (!mounted || generation != _imageLoadGeneration) {
        return;
      }
      setState(() => _faceImage = image);
    } catch (error) {
      debugPrint('[VoiceRoom] mood portrait unavailable: $error');
    }
  }

  void _scheduleBlink() {
    _nextBlink?.cancel();
    final delay = Duration(milliseconds: 2100 + _random.nextInt(2800));
    _nextBlink = Timer(delay, () async {
      if (!mounted || widget.phase == _RoomPhase.ending) {
        return;
      }
      await _blink.forward(from: 0);
      await _blink.reverse();
      if (mounted && _random.nextDouble() < 0.16) {
        await Future<void>.delayed(const Duration(milliseconds: 95));
        if (mounted) {
          await _blink.forward(from: 0);
          await _blink.reverse();
        }
      }
      if (mounted) {
        _scheduleBlink();
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final pulse =
        1 + (widget.breath - 0.5) * (0.018 + widget.level.clamp(0, 1) * 0.025);
    return TweenAnimationBuilder<double>(
      tween: Tween<double>(end: _mouthLevel),
      duration: const Duration(milliseconds: 96),
      curve: Curves.easeOutCubic,
      builder: (context, smoothedMouth, _) => AnimatedBuilder(
        animation: _blink,
        builder: (context, _) => RepaintBoundary(
          child: SizedBox.square(
            dimension: 330,
            child: Center(
              child: TweenAnimationBuilder<double>(
                tween: Tween<double>(end: _diameter),
                duration: const Duration(milliseconds: 520),
                curve: Curves.easeInOutCubicEmphasized,
                builder: (context, diameter, _) => Transform.translate(
                  offset: Offset(
                    sin(widget.progress * pi * 4) *
                        (widget.phase == _RoomPhase.speaking ? 1.8 : 0.7),
                    cos(widget.progress * pi * 2) * 2.4,
                  ),
                  child: Transform.rotate(
                    angle: _headTilt,
                    child: Transform.scale(
                      scale: pulse,
                      child: SizedBox.square(
                        dimension: diameter,
                        child: Stack(
                          fit: StackFit.expand,
                          clipBehavior: Clip.none,
                          children: [
                            CustomPaint(
                              isComplex: true,
                              willChange: true,
                              painter: _MoodOrbGlowPainter(
                                phase: widget.phase,
                                progress: widget.progress,
                                activity: _activity,
                                level: widget.level,
                              ),
                            ),
                            Padding(
                              padding: EdgeInsets.all(max(3, diameter * 0.018)),
                              child: ClipOval(
                                child: Stack(
                                  fit: StackFit.expand,
                                  children: [
                                    if (_faceImage != null &&
                                        _speechMouthImage != null &&
                                        _faceShader != null)
                                      CustomPaint(
                                        isComplex: true,
                                        willChange: true,
                                        painter: _DigitalXiaoyouPainter(
                                          image: _faceImage!,
                                          speechMouthImage: _speechMouthImage!,
                                          fragmentShader: _faceShader!,
                                          blink: Curves.easeInCubic.transform(
                                            _blink.value,
                                          ),
                                          mouth: smoothedMouth,
                                          activity: _activity,
                                          progress: widget.progress,
                                        ),
                                      )
                                    else if (_faceImage != null)
                                      RawImage(
                                        image: _faceImage,
                                        fit: BoxFit.cover,
                                        filterQuality: FilterQuality.medium,
                                      )
                                    else
                                      const DecoratedBox(
                                        decoration: BoxDecoration(
                                          gradient: RadialGradient(
                                            center: Alignment(-0.28, -0.34),
                                            radius: 0.92,
                                            colors: [
                                              Color(0xfffff7fb),
                                              Color(0xffeadfff),
                                              Color(0xffc8ecf5),
                                            ],
                                          ),
                                        ),
                                      ),
                                    CustomPaint(
                                      isComplex: true,
                                      willChange: true,
                                      painter: _MoodOrbSurfacePainter(
                                        phase: widget.phase,
                                        progress: widget.progress,
                                        activity: _activity * 0.12,
                                        level: widget.level,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _DigitalXiaoyouPainter extends CustomPainter {
  const _DigitalXiaoyouPainter({
    required this.image,
    required this.speechMouthImage,
    required this.fragmentShader,
    required this.blink,
    required this.mouth,
    required this.activity,
    required this.progress,
  });

  final ui.Image image;
  final ui.Image speechMouthImage;
  final ui.FragmentShader fragmentShader;
  final double blink;
  final double mouth;
  final double activity;
  final double progress;

  @override
  void paint(Canvas canvas, Size size) {
    fragmentShader
      ..setFloat(0, size.width)
      ..setFloat(1, size.height)
      ..setFloat(2, blink)
      ..setFloat(3, mouth)
      ..setFloat(4, activity)
      ..setFloat(5, progress)
      ..setImageSampler(0, image)
      ..setImageSampler(1, speechMouthImage);
    canvas.drawRect(
      Offset.zero & size,
      Paint()..shader = fragmentShader,
    );
  }

  @override
  bool shouldRepaint(covariant _DigitalXiaoyouPainter oldDelegate) =>
      oldDelegate.image != image ||
      oldDelegate.speechMouthImage != speechMouthImage ||
      oldDelegate.fragmentShader != fragmentShader ||
      oldDelegate.blink != blink ||
      oldDelegate.mouth != mouth ||
      oldDelegate.activity != activity ||
      oldDelegate.progress != progress;
}

class _MoodOrbGlowPainter extends CustomPainter {
  const _MoodOrbGlowPainter({
    required this.phase,
    required this.progress,
    required this.activity,
    required this.level,
  });

  final _RoomPhase phase;
  final double progress;
  final double activity;
  final double level;

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.shortestSide * 0.48;
    final wave = sin(progress * pi * 4.0) * 0.5 + 0.5;
    final accent = phase == _RoomPhase.userSpeaking
        ? const Color(0xff83e9ff)
        : phase == _RoomPhase.speaking
            ? const Color(0xffff6eaf)
            : const Color(0xffb69af4);
    canvas.drawCircle(
      center + Offset(0, radius * 0.94),
      radius * 0.78,
      Paint()
        ..shader = RadialGradient(
          colors: [
            const Color(0xff331e35).withValues(alpha: 0.25),
            Colors.transparent,
          ],
        ).createShader(
          Rect.fromCircle(
            center: center + Offset(0, radius),
            radius: radius,
          ),
        )
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 18),
    );
    for (var index = 0; index < 3; index++) {
      final spread =
          radius * (1.05 + index * 0.09 + wave * 0.015 + level * 0.025);
      canvas.drawCircle(
        center,
        spread,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3.2 - index * 0.7
          ..color = accent.withValues(
            alpha: (0.22 - index * 0.045) * activity,
          ),
      );
    }
  }

  @override
  bool shouldRepaint(covariant _MoodOrbGlowPainter oldDelegate) =>
      oldDelegate.phase != phase ||
      oldDelegate.progress != progress ||
      oldDelegate.activity != activity ||
      oldDelegate.level != level;
}

class _MoodOrbSurfacePainter extends CustomPainter {
  const _MoodOrbSurfacePainter({
    required this.phase,
    required this.progress,
    required this.activity,
    required this.level,
  });

  final _RoomPhase phase;
  final double progress;
  final double activity;
  final double level;

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.shortestSide / 2;
    final t = progress * pi * 2;
    final rect = Offset.zero & size;

    canvas.drawRect(
      rect,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(-0.48, -0.58),
          focal: const Alignment(-0.62, -0.7),
          focalRadius: 0.03,
          radius: 1.24,
          colors: [
            Colors.white.withValues(alpha: 0.42),
            Colors.transparent,
            const Color(0xff342142).withValues(alpha: 0.13),
            const Color(0xff160d22).withValues(alpha: 0.38),
          ],
          stops: const [0, 0.27, 0.7, 1],
        ).createShader(rect),
    );

    final ribbons = [
      (
        const Color(0xff73e8ff),
        sin(t * 1.0) * radius * 0.30,
        cos(t * 1.7) * radius * 0.22,
        0.72,
      ),
      (
        const Color(0xffff74ae),
        cos(t * 1.3 + 1.2) * radius * 0.32,
        sin(t * 0.8 + 0.4) * radius * 0.28,
        0.62,
      ),
      (
        const Color(0xffaa8cff),
        sin(t * 0.7 + 2.4) * radius * 0.26,
        cos(t * 1.1 + 0.8) * radius * 0.32,
        0.58,
      ),
    ];
    for (var index = 0; index < ribbons.length; index++) {
      final ribbon = ribbons[index];
      final movingCenter = center + Offset(ribbon.$2, ribbon.$3);
      final width = radius * (1.22 + sin(t * 1.9 + index) * 0.18);
      final height = radius * (0.48 + cos(t * 1.4 + index * 1.7) * 0.11);
      canvas.save();
      canvas.translate(movingCenter.dx, movingCenter.dy);
      canvas.rotate(
        sin(t * (0.8 + index * 0.17) + index) * 0.72,
      );
      canvas.drawOval(
        Rect.fromCenter(
          center: Offset.zero,
          width: width,
          height: height,
        ),
        Paint()
          ..shader = RadialGradient(
            colors: [
              Colors.white.withValues(alpha: 0.34 * activity),
              ribbon.$1.withValues(alpha: ribbon.$4 * activity),
              ribbon.$1.withValues(alpha: 0.04),
              Colors.transparent,
            ],
            stops: const [0, 0.28, 0.72, 1],
          ).createShader(
            Rect.fromCenter(
              center: Offset.zero,
              width: width,
              height: height,
            ),
          )
          ..blendMode = BlendMode.screen,
      );
      canvas.restore();
    }

    final highlightCenter = Offset(
      size.width * (0.31 + sin(t * 0.7) * 0.018),
      size.height * (0.24 + cos(t * 0.9) * 0.015),
    );
    canvas.drawOval(
      Rect.fromCenter(
        center: highlightCenter,
        width: radius * 0.74,
        height: radius * 0.25,
      ),
      Paint()
        ..shader = LinearGradient(
          colors: [
            Colors.white.withValues(alpha: 0.04),
            Colors.white.withValues(alpha: 0.56),
            Colors.white.withValues(alpha: 0.02),
          ],
        ).createShader(rect)
        ..blendMode = BlendMode.screen
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 6),
    );

    canvas.drawCircle(
      center,
      radius - 2,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 3.4
        ..shader = SweepGradient(
          transform: GradientRotation(sin(t * 0.65) * 0.9),
          colors: const [
            Color(0xfff9f7ff),
            Color(0xff7eefff),
            Color(0xffff72ad),
            Color(0xffaf98ff),
            Color(0xfff9f7ff),
          ],
        ).createShader(rect),
    );
    canvas.drawCircle(
      center,
      radius - 5.2,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 0.8
        ..color = Colors.white.withValues(alpha: 0.76),
    );
  }

  @override
  bool shouldRepaint(covariant _MoodOrbSurfacePainter oldDelegate) =>
      oldDelegate.phase != phase ||
      oldDelegate.progress != progress ||
      oldDelegate.activity != activity ||
      oldDelegate.level != level;
}

class _LegacyVoiceOrb extends StatefulWidget {
  const _LegacyVoiceOrb({
    required this.phase,
    required this.level,
    required this.progress,
    required this.breath,
    required this.onTap,
  });

  final _RoomPhase phase;
  final double level;
  final double progress;
  final double breath;
  final VoidCallback onTap;

  @override
  State<_LegacyVoiceOrb> createState() => _LegacyVoiceOrbState();
}

class _LegacyVoiceOrbState extends State<_LegacyVoiceOrb> {
  ui.FragmentShader? _shader;

  _RoomPhase get phase => widget.phase;
  double get level => widget.level;
  double get progress => widget.progress;
  double get breath => widget.breath;
  VoidCallback get onTap => widget.onTap;

  double get _targetActivity => switch (widget.phase) {
        _RoomPhase.connecting => 0.12,
        _RoomPhase.listening => 0.28,
        _RoomPhase.userSpeaking => 0.74,
        _RoomPhase.thinking => 0.62,
        _RoomPhase.speaking => 0.92,
        _RoomPhase.interrupted => 0.42,
        _RoomPhase.ending => 0.1,
      };

  @override
  void initState() {
    super.initState();
    unawaited(_loadShader());
  }

  Future<void> _loadShader() async {
    final program = await ui.FragmentProgram.fromAsset(
      'shaders/voice_orb.frag',
    );
    if (!mounted) {
      return;
    }
    setState(() => _shader = program.fragmentShader());
  }

  @override
  Widget build(BuildContext context) {
    return TweenAnimationBuilder<double>(
      tween: Tween<double>(end: _targetActivity),
      duration: const Duration(milliseconds: 560),
      curve: Curves.easeInOutCubic,
      builder: (context, activity, _) => TweenAnimationBuilder<double>(
        tween: Tween<double>(end: level),
        duration: const Duration(milliseconds: 140),
        curve: Curves.easeOutCubic,
        builder: (context, smoothedLevel, _) => Semantics(
          button: true,
          label: phase == _RoomPhase.userSpeaking ? '正在听你说' : '语音状态',
          child: GestureDetector(
            onTap: onTap,
            child: RepaintBoundary(
              child: SizedBox.square(
                dimension: 300,
                child: CustomPaint(
                  painter: _LegacyVoiceOrbPainter(
                    activity: activity,
                    level: smoothedLevel,
                    progress: progress,
                    breath: breath,
                    fragmentShader: _shader,
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _LegacyVoiceOrbPainter extends CustomPainter {
  const _LegacyVoiceOrbPainter({
    required this.activity,
    required this.level,
    required this.progress,
    required this.breath,
    required this.fragmentShader,
  });

  final double activity;
  final double level;
  final double progress;
  final double breath;
  final ui.FragmentShader? fragmentShader;

  static const _petalColors = [
    Color(0xff6aa7ff),
    Color(0xffff5c9b),
    Color(0xff18dce8),
    Color(0xff78edcf),
  ];

  @override
  void paint(Canvas canvas, Size size) {
    final liveShader = fragmentShader;
    if (liveShader != null) {
      liveShader
        ..setFloat(0, size.width)
        ..setFloat(1, size.height)
        ..setFloat(2, progress * pi * 2)
        ..setFloat(3, activity)
        ..setFloat(4, level)
        ..setFloat(5, breath);
      canvas.drawRect(
        Offset.zero & size,
        Paint()..shader = liveShader,
      );
      return;
    }

    final center = size.center(Offset.zero);
    final intensity = (activity + level * 0.22).clamp(0.08, 1.0);
    final t = progress * pi * 2;
    final baseRadius = size.shortestSide *
        (0.278 + breath * 0.008 + sin(t * 2) * 0.003 + level * 0.008);
    final sphereRect = Rect.fromCircle(center: center, radius: baseRadius);
    final surface = Path()..addOval(sphereRect);

    final shadowCenter = center + Offset(0, baseRadius * 1.04);
    canvas.drawOval(
      Rect.fromCenter(
        center: shadowCenter,
        width: baseRadius * 1.56,
        height: baseRadius * 0.3,
      ),
      Paint()
        ..shader = RadialGradient(
          colors: [
            const Color(0xff051525).withValues(alpha: 0.34),
            const Color(0xff52647e).withValues(alpha: 0.11),
            Colors.transparent,
          ],
        ).createShader(
            Rect.fromCircle(center: shadowCenter, radius: baseRadius))
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 12),
    );

    final haloRect = Rect.fromCircle(center: center, radius: baseRadius * 1.62);
    canvas.drawCircle(
      center,
      baseRadius * (1.34 + intensity * 0.12),
      Paint()
        ..shader = RadialGradient(
          colors: [
            const Color(0xff55dff4).withValues(alpha: 0.13 + intensity * 0.08),
            const Color(0xffff68ae).withValues(alpha: 0.07),
            Colors.transparent,
          ],
          stops: const [0, 0.52, 1],
        ).createShader(haloRect)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 16),
    );

    // Avoid saveLayer here: rotating a full off-screen layer every frame was
    // the main source of stutter on mid-range Android GPUs.
    canvas.save();
    canvas.clipPath(surface);
    canvas.drawRect(
      sphereRect,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(-0.26, -0.3),
          focal: const Alignment(-0.34, -0.4),
          focalRadius: 0.04,
          radius: 1.04,
          colors: const [
            Color(0xff315c76),
            Color(0xff12344f),
            Color(0xff061625),
            Color(0xff020812),
          ],
          stops: const [0, 0.36, 0.73, 1],
        ).createShader(sphereRect),
    );

    // Broad aurora ribbons rotate clockwise.
    for (var index = 0; index < 3; index++) {
      final angle = t + index * pi * 2 / 3 + sin(t * 2 + index * 1.31) * 0.2;
      _drawLiquidPetal(
        canvas,
        center: center,
        angle: angle,
        length: baseRadius *
            (0.78 + sin(t * 3 + index * 1.4) * 0.075 + level * 0.035),
        width: baseRadius *
            (0.52 + cos(t * 2 + index * 1.7) * 0.075 + intensity * 0.025),
        color: _petalColors[index],
        opacity: 0.22 + intensity * 0.11,
        bend: sin(t * 2 + index * 2.1) * 0.24,
      );
    }

    // A second, brighter set counter-rotates at a different cadence. Both
    // speeds are integer cycles in the same 12-second loop, so the seam is
    // mathematically identical while the motion never looks one-directional.
    for (var index = 0; index < 4; index++) {
      final reverseAngle =
          -t * 2 + index * pi / 2 + cos(t * 3 + index * 0.93) * 0.18;
      _drawLiquidPetal(
        canvas,
        center: center,
        angle: reverseAngle,
        length: baseRadius *
            (0.63 + cos(t * 2 + index * 1.23) * 0.065 + level * 0.05),
        width: baseRadius *
            (0.34 + sin(t * 3 + index * 1.51) * 0.045 + intensity * 0.02),
        color: _petalColors[index],
        opacity: 0.42 + intensity * 0.18,
        bend: cos(t * 2 + index * 1.8) * 0.2,
      );
    }

    final corePulse = baseRadius * (0.22 + sin(t * 2) * 0.012);
    canvas.drawCircle(
      center,
      corePulse,
      Paint()
        ..shader = RadialGradient(
          colors: [
            Colors.white.withValues(alpha: 0.98),
            const Color(0xffe8fdff).withValues(alpha: 0.74),
            const Color(0xff69dfff).withValues(alpha: 0.22),
            Colors.transparent,
          ],
          stops: const [0, 0.22, 0.58, 1],
        ).createShader(
          Rect.fromCircle(center: center, radius: corePulse),
        )
        ..blendMode = BlendMode.plus,
    );
    canvas.drawCircle(
      center,
      baseRadius * (0.052 + intensity * 0.009),
      Paint()
        ..color = Colors.white
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3),
    );

    // Refractive edge falloff keeps the ribbons inside a glass volume.
    canvas.drawRect(
      sphereRect,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(-0.24, -0.3),
          radius: 1.03,
          colors: [
            Colors.white.withValues(alpha: 0.05),
            Colors.transparent,
            Colors.transparent,
            const Color(0xff00040c).withValues(alpha: 0.5),
          ],
          stops: const [0, 0.3, 0.68, 1],
        ).createShader(sphereRect),
    );

    canvas.save();
    canvas.translate(
      center.dx - baseRadius * 0.33,
      center.dy - baseRadius * 0.39,
    );
    canvas.rotate(-0.58);
    canvas.drawOval(
      Rect.fromCenter(
        center: Offset.zero,
        width: baseRadius * 0.64,
        height: baseRadius * 0.16,
      ),
      Paint()
        ..shader = LinearGradient(
          colors: [
            Colors.white.withValues(alpha: 0.02),
            Colors.white.withValues(alpha: 0.42),
            Colors.white.withValues(alpha: 0.03),
          ],
        ).createShader(
          Rect.fromCenter(
            center: Offset.zero,
            width: baseRadius * 0.68,
            height: baseRadius * 0.18,
          ),
        )
        ..blendMode = BlendMode.screen
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3),
    );
    canvas.restore();
    canvas.restore();

    canvas.drawPath(
      surface,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 4.4
        ..shader = SweepGradient(
          transform: GradientRotation(-t * 2),
          colors: [
            const Color(0xff071326).withValues(alpha: 0.92),
            const Color(0xff66f5ff).withValues(alpha: 0.82),
            Colors.white.withValues(alpha: 0.86),
            const Color(0xffff6ea9).withValues(alpha: 0.78),
            const Color(0xff071326).withValues(alpha: 0.92),
          ],
        ).createShader(
          Rect.fromCircle(center: center, radius: baseRadius * 1.15),
        )
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 1),
    );

    canvas.drawArc(
      Rect.fromCircle(center: center, radius: baseRadius * 1.015),
      t + pi * 0.68,
      pi * 0.54,
      false,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeWidth = 1.8
        ..color = const Color(0xff78f7ff).withValues(alpha: 0.78)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 2),
    );
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: baseRadius * 1.015),
      -t * 2 + pi * 1.7,
      pi * 0.38,
      false,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeWidth = 1.6
        ..color = const Color(0xffff77ad).withValues(alpha: 0.72)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 2),
    );
    canvas.drawCircle(
      center,
      baseRadius,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 0.9
        ..color = Colors.white.withValues(alpha: 0.68),
    );
  }

  void _drawLiquidPetal(
    Canvas canvas, {
    required Offset center,
    required double angle,
    required double length,
    required double width,
    required Color color,
    required double opacity,
    required double bend,
  }) {
    final path = Path()
      ..moveTo(-length * 0.1, 0)
      ..cubicTo(
        length * 0.15,
        -width * (0.68 - bend),
        length * 0.7,
        -width * (0.5 + bend * 0.25),
        length,
        0,
      )
      ..cubicTo(
        length * 0.68,
        width * (0.42 - bend * 0.18),
        length * 0.12,
        width * (0.58 + bend),
        -length * 0.1,
        0,
      )
      ..close();
    final bounds = Rect.fromLTRB(
      -length * 0.12,
      -width * 0.72,
      length * 1.04,
      width * 0.72,
    );
    canvas.save();
    canvas.translate(center.dx, center.dy);
    canvas.rotate(angle);
    canvas.scale(1, 1 + bend * 0.18);
    canvas.drawPath(
      path,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(-0.62, 0),
          focal: const Alignment(-0.42, 0),
          focalRadius: 0.05,
          radius: 1.08,
          colors: [
            Colors.white.withValues(alpha: opacity * 0.82),
            color.withValues(alpha: opacity),
            color.withValues(alpha: opacity * 0.34),
            Colors.transparent,
          ],
          stops: const [0, 0.34, 0.72, 1],
        ).createShader(bounds)
        ..blendMode = BlendMode.plus,
    );

    final filament = Path()
      ..moveTo(-length * 0.03, 0)
      ..cubicTo(
        length * 0.28,
        -width * bend * 0.52,
        length * 0.67,
        width * bend * 0.36,
        length * 0.88,
        0,
      );
    canvas.drawPath(
      filament,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeWidth = width * 0.055
        ..shader = LinearGradient(
          colors: [
            Colors.white.withValues(alpha: opacity * 0.82),
            color.withValues(alpha: opacity * 0.64),
            Colors.transparent,
          ],
        ).createShader(bounds)
        ..blendMode = BlendMode.plus,
    );
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant _LegacyVoiceOrbPainter oldDelegate) =>
      oldDelegate.activity != activity ||
      oldDelegate.level != level ||
      oldDelegate.progress != progress ||
      oldDelegate.breath != breath ||
      oldDelegate.fragmentShader != fragmentShader;
}

class _VoiceRoomBackgroundPainter extends CustomPainter {
  const _VoiceRoomBackgroundPainter(this.progress);

  final double progress;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [
          Color(0xfffffbfd),
          Color(0xffffeef6),
          Color(0xffeee9fa),
        ],
      ).createShader(Offset.zero & size);
    canvas.drawRect(Offset.zero & size, paint);

    final t = progress * pi * 2;
    final particlePaint = Paint()..color = const Color(0x66ffffff);
    for (var index = 0; index < 20; index++) {
      final baseX = ((index * 73.0) % size.width);
      final baseY = ((index * 131.0) % size.height);
      final frequency = 1 + index % 3;
      final phase = index * 0.83;
      final x = baseX + sin(t * frequency + phase) * 9;
      final y = baseY + cos(t * frequency + phase) * 14;
      canvas.drawCircle(Offset(x, y), 1.2 + index % 3, particlePaint);
    }
  }

  @override
  bool shouldRepaint(covariant _VoiceRoomBackgroundPainter oldDelegate) =>
      oldDelegate.progress != progress;
}

String _phaseLabel(_RoomPhase phase) => switch (phase) {
      _RoomPhase.connecting => '正在靠近小悠…',
      _RoomPhase.listening => '小悠在听',
      _RoomPhase.userSpeaking => '正在听你说',
      _RoomPhase.thinking => '小悠想一想…',
      _RoomPhase.speaking => '小悠正在你耳边说话',
      _RoomPhase.interrupted => '我在听，继续说',
      _RoomPhase.ending => '正在收藏这一会儿…',
    };

String _duration(int seconds) =>
    '${(seconds ~/ 60).toString().padLeft(2, '0')}:'
    '${(seconds % 60).toString().padLeft(2, '0')}';

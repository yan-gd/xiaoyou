import 'dart:async';
import 'dart:io';
import 'dart:math';
import 'dart:ui' as ui;

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'voice_recorder.dart';
import 'xiaoyou_api.dart';

const _voiceInk = Color(0xff3d2b36);
const _voiceMuted = Color(0xff9c8792);
const _voiceRose = Color(0xffb35282);
const _voiceLavender = Color(0xff8d71bd);

enum _RoomPhase { listening, recording, waiting, speaking, ending }

class VoiceRoomScreen extends StatefulWidget {
  const VoiceRoomScreen({
    super.key,
    required this.api,
    required this.initialEventSequence,
  });

  final XiaoyouApi api;
  final int initialEventSequence;

  @override
  State<VoiceRoomScreen> createState() => _VoiceRoomScreenState();
}

class _VoiceRoomScreenState extends State<VoiceRoomScreen>
    with TickerProviderStateMixin {
  final _recorder = VoiceRecorderController();
  final _player = AudioPlayer();
  final _lines = <_VoiceRoomLine>[];
  StreamSubscription<double>? _amplitude;
  StreamSubscription<void>? _playbackCompleted;
  Timer? _clock;
  late final AnimationController _breath = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2600),
  )..repeat(reverse: true);
  late final AnimationController _particles = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 12),
  )..repeat();

  _RoomPhase _phase = _RoomPhase.listening;
  double _level = 0.08;
  int _elapsedSeconds = 0;
  String _roomId = '';
  bool _initializing = true;
  bool _closed = false;

  @override
  void initState() {
    super.initState();
    _clock = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) {
        setState(() => _elapsedSeconds += 1);
      }
    });
    _playbackCompleted = _player.onPlayerComplete.listen((_) {
      if (mounted && _phase == _RoomPhase.speaking) {
        setState(() {
          _phase = _RoomPhase.listening;
          _level = 0.08;
        });
      }
    });
    unawaited(_recorder.prepare());
    unawaited(_initializeRoom());
  }

  @override
  void dispose() {
    _closed = true;
    _clock?.cancel();
    _amplitude?.cancel();
    _playbackCompleted?.cancel();
    _breath.dispose();
    _particles.dispose();
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
      });
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

  Future<void> _toggleTalk() async {
    if (_phase == _RoomPhase.ending || _initializing) {
      return;
    }
    if (_roomId.isEmpty) {
      await _initializeRoom();
      if (_roomId.isEmpty) {
        return;
      }
    }
    if (_phase == _RoomPhase.recording) {
      await _finishTurn();
      return;
    }
    if (_phase == _RoomPhase.speaking) {
      await _player.stop();
    }
    final started = await _recorder.start(pcm16Wav: true);
    if (!mounted) {
      return;
    }
    if (!started) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('需要麦克风权限才能进入语音房间')),
      );
      return;
    }
    await _amplitude?.cancel();
    _amplitude = _recorder.amplitudeStream().listen((decibels) {
      if (!mounted || _phase != _RoomPhase.recording) {
        return;
      }
      setState(() => _level = ((decibels + 55) / 55).clamp(0.08, 1));
    });
    setState(() {
      _phase = _RoomPhase.recording;
      _level = 0.12;
    });
    HapticFeedback.mediumImpact();
  }

  Future<void> _finishTurn() async {
    await _amplitude?.cancel();
    _amplitude = null;
    final recorded = await _recorder.stop();
    if (recorded == null || recorded.durationMs < 550) {
      if (mounted) {
        setState(() {
          _phase = _RoomPhase.listening;
          _level = 0.08;
        });
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('再多说一点点，小悠才听得清')),
        );
      }
      return;
    }
    setState(() {
      _phase = _RoomPhase.waiting;
      _level = 0.18;
    });
    final file = File(recorded.path);
    late final VoiceRoomTurnResult result;
    try {
      result = await widget.api.sendVoiceRoomTurn(
        roomId: _roomId,
        turnId: 'room-${DateTime.now().microsecondsSinceEpoch}',
        audioBytes: await file.readAsBytes(),
        mimeType: recorded.mimeType,
        durationMs: recorded.durationMs,
      );
      if (!result.accepted && !result.duplicate) {
        throw const HttpException('voice_not_accepted');
      }
    } catch (_) {
      if (mounted) {
        setState(() => _phase = _RoomPhase.listening);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('刚才那句话没有送到，再说一次好吗')),
        );
      }
      return;
    } finally {
      if (await file.exists()) {
        await file.delete();
      }
    }
    if (!mounted) {
      return;
    }
    setState(() {
      _lines
        ..add(
          _VoiceRoomLine(
            fromXiaoyou: false,
            text: result.turn.userText,
          ),
        )
        ..add(
          _VoiceRoomLine(
            fromXiaoyou: true,
            text: result.turn.assistantText,
          ),
        );
    });
    if (result.turn.audioMediaId.isNotEmpty) {
      try {
        final media = await widget.api.downloadMedia(
          result.turn.audioMediaId,
        );
        if (_closed) {
          return;
        }
        await _player.play(
          BytesSource(media.bytes, mimeType: media.mimeType),
        );
        if (mounted) {
          setState(() {
            _phase = _RoomPhase.speaking;
            _level = 0.58;
          });
        }
      } catch (_) {
        if (!mounted) {
          return;
        }
        setState(() {
          _phase = _RoomPhase.listening;
          _level = 0.08;
        });
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('小悠的回复已保存，但这次音频没有播放出来')),
        );
      }
    } else if (mounted) {
      setState(() {
        _phase = _RoomPhase.listening;
        _level = 0.08;
      });
    }
  }

  Future<void> _endRoom() async {
    if (_phase == _RoomPhase.ending) {
      return;
    }
    final wasRecording = _phase == _RoomPhase.recording;
    setState(() => _phase = _RoomPhase.ending);
    if (wasRecording) {
      await _recorder.cancel();
    }
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
                          animation: Listenable.merge([_breath, _particles]),
                          builder: (_, __) => _VoiceOrb(
                            phase: _phase,
                            level: _level,
                            progress: _particles.value,
                            breath: Curves.easeInOutSine.transform(
                              _breath.value,
                            ),
                            onTap: _toggleTalk,
                          ),
                        ),
                        const SizedBox(height: 28),
                        AnimatedSwitcher(
                          duration: const Duration(milliseconds: 320),
                          child: Text(
                            _initializing ? '正在连接小悠…' : _phaseLabel(_phase),
                            key: ValueKey('$_phase-$_initializing'),
                            style: const TextStyle(
                              color: _voiceInk,
                              fontSize: 18,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(
                          _phase == _RoomPhase.recording
                              ? '轻触结束这一句话'
                              : '轻触光球开始说话',
                          style: const TextStyle(
                            color: _voiceMuted,
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ),
                  ),
                  if (_lines.isNotEmpty)
                    Container(
                      constraints: const BoxConstraints(maxHeight: 180),
                      margin: const EdgeInsets.fromLTRB(18, 0, 18, 14),
                      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
                      decoration: BoxDecoration(
                        color: const Color(0xd9ffffff),
                        borderRadius: BorderRadius.circular(26),
                        border: Border.all(color: const Color(0xb3ffffff)),
                      ),
                      child: ListView(
                        reverse: true,
                        shrinkWrap: true,
                        children: _lines.reversed
                            .take(4)
                            .map(
                              (line) => Padding(
                                padding:
                                    const EdgeInsets.symmetric(vertical: 5),
                                child: Text(
                                  '${line.fromXiaoyou ? '小悠' : 'YoYo'} · ${line.text}',
                                  maxLines: 2,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(
                                    color: line.fromXiaoyou
                                        ? _voiceInk
                                        : _voiceLavender,
                                    height: 1.35,
                                  ),
                                ),
                              ),
                            )
                            .toList(),
                      ),
                    ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(18, 0, 18, 20),
                    child: OutlinedButton.icon(
                      onPressed: _endRoom,
                      icon: const Icon(Icons.stop_circle_outlined),
                      label: const Text('结束并留下纪念卡'),
                      style: OutlinedButton.styleFrom(
                        minimumSize: const Size.fromHeight(48),
                        foregroundColor: _voiceRose,
                        side: const BorderSide(color: Color(0x55b35282)),
                      ),
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
                            const SizedBox(height: 8),
                            _TranscriptBubble(
                              mine: false,
                              label: '小悠',
                              text: turn.assistantText,
                            ),
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

class _VoiceOrb extends StatefulWidget {
  const _VoiceOrb({
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
  State<_VoiceOrb> createState() => _VoiceOrbState();
}

class _VoiceOrbState extends State<_VoiceOrb> {
  ui.FragmentShader? _shader;

  _RoomPhase get phase => widget.phase;
  double get level => widget.level;
  double get progress => widget.progress;
  double get breath => widget.breath;
  VoidCallback get onTap => widget.onTap;

  double get _targetActivity => switch (widget.phase) {
        _RoomPhase.listening => 0.28,
        _RoomPhase.recording => 0.74,
        _RoomPhase.waiting => 0.62,
        _RoomPhase.speaking => 0.92,
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
          label: phase == _RoomPhase.recording ? '结束这句话' : '开始说话',
          child: GestureDetector(
            onTap: onTap,
            child: RepaintBoundary(
              child: SizedBox.square(
                dimension: 300,
                child: CustomPaint(
                  painter: _VoiceOrbPainter(
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

class _VoiceOrbPainter extends CustomPainter {
  const _VoiceOrbPainter({
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
  bool shouldRepaint(covariant _VoiceOrbPainter oldDelegate) =>
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
    for (var index = 0; index < 32; index++) {
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

class _VoiceRoomLine {
  const _VoiceRoomLine({required this.fromXiaoyou, required this.text});

  final bool fromXiaoyou;
  final String text;
}

String _phaseLabel(_RoomPhase phase) => switch (phase) {
      _RoomPhase.listening => '小悠在听',
      _RoomPhase.recording => '正在听你说',
      _RoomPhase.waiting => '小悠想一想…',
      _RoomPhase.speaking => '小悠正在你耳边说话',
      _RoomPhase.ending => '正在收藏这一会儿…',
    };

String _duration(int seconds) =>
    '${(seconds ~/ 60).toString().padLeft(2, '0')}:'
    '${(seconds % 60).toString().padLeft(2, '0')}';

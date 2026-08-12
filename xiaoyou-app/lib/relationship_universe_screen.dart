import 'dart:async';
import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'achievement_screen.dart';
import 'chat_models.dart';
import 'daily_journal_screen.dart';
import 'relationship_models.dart';
import 'time_capsule_screen.dart';
import 'theme_controller.dart';
import 'xiaoyou_api.dart';

const _orbitInk = Color(0xff3d2b36);
const _orbitMuted = Color(0xff99858f);
const _orbitRose = Color(0xffad4f7f);
const _orbitGold = Color(0xffd8b477);
const _avatarAsset = 'assets/xiaoyou-avatar.png';

class RelationshipUniverseScreen extends StatefulWidget {
  const RelationshipUniverseScreen({
    super.key,
    required this.api,
    required this.messages,
    required this.favoriteMessageIds,
  });

  final XiaoyouApi api;
  final List<ChatMessage> messages;
  final Set<String> favoriteMessageIds;

  @override
  State<RelationshipUniverseScreen> createState() =>
      _RelationshipUniverseScreenState();
}

class _RelationshipUniverseScreenState extends State<RelationshipUniverseScreen>
    with TickerProviderStateMixin {
  late final AnimationController _stars = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 14),
  )..repeat();
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2400),
  )..repeat(reverse: true);
  final _transformation = TransformationController();

  List<RelationshipEntry> _entries = const [];
  List<RelationshipOrbitEvent> _events = const [];
  RelationshipOrbitEvent? _selected;
  Size _orbitViewport = Size.zero;
  bool _orbitViewInitialized = false;
  bool _loading = true;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _stars.dispose();
    _pulse.dispose();
    _transformation.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    if (mounted) {
      setState(() {
        _loading = true;
        _error = '';
      });
    }
    try {
      final entries = await widget.api.relationshipEntries();
      final events = RelationshipOrbitBuilder.build(
        messages: widget.messages,
        entries: entries,
        favoriteMessageIds: widget.favoriteMessageIds,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _entries = entries;
        _events = events;
        _selected = _preferredSelection(events);
        _loading = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      final fallbackEvents = RelationshipOrbitBuilder.build(
        messages: widget.messages,
        entries: const [],
        favoriteMessageIds: widget.favoriteMessageIds,
      );
      setState(() {
        _events = fallbackEvents;
        _selected = _preferredSelection(fallbackEvents);
        _loading = false;
        _error = '关系卡片暂时没有同步，已展示本机聊天足迹';
      });
    }
  }

  RelationshipOrbitEvent? _preferredSelection(
    List<RelationshipOrbitEvent> events,
  ) {
    return events.reversed
            .where(
              (event) =>
                  event.message?.kind == 'image' ||
                  event.message?.kind == 'voice',
            )
            .firstOrNull ??
        events.lastOrNull;
  }

  List<RelationshipOrbitEvent> get _posterEvents {
    final occurredAt = widget.messages.lastOrNull?.timestamp ?? DateTime.now();
    final portals = <RelationshipEventKind, RelationshipOrbitEvent>{
      RelationshipEventKind.firstLight: RelationshipOrbitEvent(
        id: 'portal-first-light',
        kind: RelationshipEventKind.firstLight,
        title: '故事开始的地方',
        subtitle: '第一句话会在这里点亮',
        occurredAt: occurredAt,
      ),
      RelationshipEventKind.journal: RelationshipOrbitEvent(
        id: 'portal-journal',
        kind: RelationshipEventKind.journal,
        title: '我们今天',
        subtitle: '把今天写进共同日记',
        occurredAt: occurredAt,
      ),
      RelationshipEventKind.capsule: RelationshipOrbitEvent(
        id: 'portal-capsule',
        kind: RelationshipEventKind.capsule,
        title: '写给未来的信',
        subtitle: '等约定的那一天再拆开',
        occurredAt: occurredAt,
      ),
      RelationshipEventKind.anniversary: RelationshipOrbitEvent(
        id: 'portal-anniversary',
        kind: RelationshipEventKind.anniversary,
        title: '相伴的日子',
        subtitle: '普通日子也会慢慢连成星座',
        occurredAt: occurredAt,
      ),
      RelationshipEventKind.photo: RelationshipOrbitEvent(
        id: 'portal-photo',
        kind: RelationshipEventKind.photo,
        title: '最近的小悠',
        subtitle: '共同珍藏的照片会漂到这里',
        occurredAt: occurredAt,
      ),
      RelationshipEventKind.conversation: RelationshipOrbitEvent(
        id: 'portal-conversation',
        kind: RelationshipEventKind.conversation,
        title: '难忘的对话',
        subtitle: '收藏一句值得重听的话',
        occurredAt: occurredAt,
      ),
      RelationshipEventKind.voiceMemory: RelationshipOrbitEvent(
        id: 'portal-voice-room',
        kind: RelationshipEventKind.voiceMemory,
        title: '耳边的一会儿',
        subtitle: '像通话一样自然地聊一会儿',
        occurredAt: occurredAt,
      ),
      RelationshipEventKind.achievement: RelationshipOrbitEvent(
        id: 'portal-achievement',
        kind: RelationshipEventKind.achievement,
        title: '新的相伴印记',
        subtitle: '下一颗成就星正在靠近',
        occurredAt: occurredAt,
        newlyUnlocked: true,
      ),
    };
    const order = [
      RelationshipEventKind.journal,
      RelationshipEventKind.capsule,
      RelationshipEventKind.anniversary,
      RelationshipEventKind.photo,
      RelationshipEventKind.conversation,
      RelationshipEventKind.voiceMemory,
      RelationshipEventKind.achievement,
      RelationshipEventKind.firstLight,
    ];
    return [
      for (final kind in order)
        _events.reversed.where((event) => event.kind == kind).firstOrNull ??
            portals[kind]!,
    ];
  }

  Future<void> _openJournal([RelationshipEntry? entry]) async {
    DailyJournal? journal;
    if (entry != null && entry.kind == 'journal') {
      journal = DailyJournal.fromEntry(entry);
    }
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => DailyJournalScreen(
          api: widget.api,
          initialJournal: journal,
        ),
      ),
    );
    await _refresh();
  }

  Future<void> _openCapsules() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => TimeCapsuleScreen(
          api: widget.api,
          entries: _entries
              .where((entry) => entry.kind == 'capsule')
              .toList(growable: false),
        ),
      ),
    );
    await _refresh();
  }

  Future<void> _openAchievements() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ChatAchievementScreen(
          messages: widget.messages,
          favoriteCount: widget.favoriteMessageIds.length,
        ),
      ),
    );
  }

  Future<void> _openSelected() async {
    final event = _selected;
    if (event == null) {
      return;
    }
    if (event.kind == RelationshipEventKind.journal) {
      await _openJournal(event.entry);
      return;
    }
    if (event.kind == RelationshipEventKind.capsule) {
      await _openCapsules();
      return;
    }
    if (event.kind == RelationshipEventKind.achievement) {
      await _openAchievements();
      return;
    }
    await showModalBottomSheet<void>(
      context: context,
      useSafeArea: true,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _OrbitMemorySheet(
        event: event,
        api: widget.api,
      ),
    );
  }

  void _resetView() {
    _applyOrbitFit(_orbitViewport);
    HapticFeedback.selectionClick();
  }

  void _scheduleOrbitFit(Size viewport) {
    if (viewport.isEmpty) {
      return;
    }
    _orbitViewport = viewport;
    if (_orbitViewInitialized) {
      return;
    }
    _orbitViewInitialized = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        _applyOrbitFit(viewport);
      }
    });
  }

  void _applyOrbitFit(Size viewport) {
    if (viewport.isEmpty) {
      return;
    }
    final scale = min(
      viewport.width / 760,
      viewport.height / 640,
    ).clamp(0.44, 0.72);
    final dx = (viewport.width - _OrbitCanvas.size.width * scale) / 2;
    final dy = (viewport.height - _OrbitCanvas.size.height * scale) / 2;
    _transformation.value = Matrix4.diagonal3Values(scale, scale, 1)
      ..setTranslationRaw(dx, dy, 0);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: xiaoyouPageSurface(context),
      body: Stack(
        children: [
          Positioned.fill(
            child: AnimatedBuilder(
              animation: _stars,
              builder: (_, __) => CustomPaint(
                painter: _UniverseBackgroundPainter(_stars.value),
              ),
            ),
          ),
          SafeArea(
            child: CustomScrollView(
              physics: const BouncingScrollPhysics(),
              slivers: [
                SliverToBoxAdapter(child: _buildHeader()),
                SliverToBoxAdapter(child: _buildOrbitCard()),
                SliverToBoxAdapter(child: _buildMemoryRibbon()),
                const SliverToBoxAdapter(child: SizedBox(height: 26)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHeader() {
    return SizedBox(
      height: 132,
      child: Stack(
        children: [
          Positioned(
            left: 18,
            top: 8,
            child: IconButton(
              onPressed: () => Navigator.of(context).pop(),
              icon: const Icon(Icons.arrow_back_ios_new_rounded, size: 18),
              style: IconButton.styleFrom(
                backgroundColor: xiaoyouIsDark(context)
                    ? const Color(0xaa242127)
                    : const Color(0x9fffffff),
                foregroundColor: _orbitRose,
                side: BorderSide(color: xiaoyouGlassBorder(context)),
              ),
            ),
          ),
          const Positioned(
            left: 66,
            top: 18,
            right: 20,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      '我们的轨道',
                      style: TextStyle(
                        color: _orbitInk,
                        fontSize: 31,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 1.2,
                      ),
                    ),
                    SizedBox(width: 9),
                    Icon(
                      Icons.auto_awesome_rounded,
                      color: Color(0xffd4a8bd),
                      size: 22,
                    ),
                  ],
                ),
                SizedBox(height: 8),
                Row(
                  children: [
                    Icon(
                      Icons.favorite_rounded,
                      color: Color(0xffde77a6),
                      size: 15,
                    ),
                    SizedBox(width: 8),
                    Text(
                      '今天的我们',
                      style: TextStyle(
                        color: _orbitMuted,
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 0.8,
                      ),
                    ),
                  ],
                ),
                SizedBox(height: 13),
                SizedBox(
                  width: 122,
                  child: Divider(
                    height: 1,
                    thickness: 1,
                    color: Color(0x52b75886),
                  ),
                ),
              ],
            ),
          ),
          Positioned(
            right: 22,
            top: 4,
            child: IgnorePointer(
              child: SizedBox(
                width: 108,
                height: 74,
                child: CustomPaint(
                  painter: _HeaderOrbitFlourish(),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildOrbitCard() {
    final posterEvents = _posterEvents;
    return SizedBox(
      height: 500,
      child: Stack(
        children: [
          Positioned.fill(
            child: AnimatedBuilder(
              animation: Listenable.merge([_stars, _pulse]),
              builder: (_, __) {
                if (_loading) {
                  return const Center(
                    child: CircularProgressIndicator(color: _orbitRose),
                  );
                }
                return LayoutBuilder(
                  builder: (_, constraints) {
                    _scheduleOrbitFit(constraints.biggest);
                    return InteractiveViewer(
                      transformationController: _transformation,
                      constrained: false,
                      boundaryMargin: const EdgeInsets.all(260),
                      minScale: 0.4,
                      maxScale: 2.65,
                      child: _OrbitCanvas(
                        events: posterEvents,
                        selected: _selected,
                        pulse: _pulse.value,
                        progress: _stars.value,
                        onSelected: (event) {
                          setState(() => _selected = event);
                          HapticFeedback.selectionClick();
                        },
                      ),
                    );
                  },
                );
              },
            ),
          ),
          if (_error.isNotEmpty)
            Positioned(
              left: 24,
              top: 5,
              child: const _SoftNotice(text: '本机足迹'),
            ),
          Positioned(
            right: 18,
            top: 55,
            child: Column(
              children: [
                _GlassIconButton(
                  icon: Icons.center_focus_strong_rounded,
                  tooltip: '回到星图中心',
                  onPressed: _resetView,
                ),
                const SizedBox(height: 8),
                _GlassIconButton(
                  icon: Icons.refresh_rounded,
                  tooltip: '刷新关系轨道',
                  onPressed: _refresh,
                ),
              ],
            ),
          ),
          Positioned(
            left: 28,
            bottom: 22,
            child: AnimatedBuilder(
              animation: _pulse,
              builder: (_, __) => Opacity(
                opacity: 0.56 + _pulse.value * 0.34,
                child: const Row(
                  children: [
                    Icon(
                      Icons.auto_awesome_rounded,
                      color: _orbitGold,
                      size: 17,
                    ),
                    SizedBox(width: 7),
                    Text(
                      '新成就解锁中…',
                      style: TextStyle(
                        color: _orbitMuted,
                        fontSize: 12,
                        letterSpacing: 0.6,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildMemoryRibbon() {
    final event = _selected;
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 420),
      switchInCurve: Curves.easeOutBack,
      switchOutCurve: Curves.easeIn,
      transitionBuilder: (child, animation) => FadeTransition(
        opacity: animation,
        child: ScaleTransition(
          scale: Tween<double>(begin: 0.96, end: 1).animate(animation),
          child: child,
        ),
      ),
      child: event == null
          ? const SizedBox.shrink()
          : _SelectedMemoryCard(
              key: ValueKey(event.id),
              event: event,
              api: widget.api,
              onTap: _openSelected,
            ),
    );
  }
}

class _OrbitCanvas extends StatelessWidget {
  const _OrbitCanvas({
    required this.events,
    required this.selected,
    required this.pulse,
    required this.progress,
    required this.onSelected,
  });

  final List<RelationshipOrbitEvent> events;
  final RelationshipOrbitEvent? selected;
  final double pulse;
  final double progress;
  final ValueChanged<RelationshipOrbitEvent> onSelected;

  static const size = Size(960, 780);

  @override
  Widget build(BuildContext context) {
    final visible = events.reversed.take(12).toList().reversed.toList();
    final positions = _positions(visible.length);
    return SizedBox(
      width: size.width,
      height: size.height,
      child: Stack(
        children: [
          Positioned.fill(
            child: CustomPaint(
              painter: _RelationshipOrbitPainter(
                count: visible.length,
                positions: positions,
                progress: progress,
                pulse: pulse,
              ),
            ),
          ),
          const Positioned(
            left: 480 - 95,
            top: 390 - 95,
            child: _OrbitHeart(),
          ),
          for (var index = 0; index < visible.length; index++)
            Positioned(
              left: positions[index].dx - 62.5,
              top: positions[index].dy - 50,
              child: _OrbitNode(
                event: visible[index],
                selected: selected?.id == visible[index].id,
                pulse: pulse,
                onTap: () => onSelected(visible[index]),
              ),
            ),
        ],
      ),
    );
  }

  List<Offset> _positions(int count) {
    if (count == 0) {
      return const [];
    }
    const posterPositions = [
      Offset(480, 82),
      Offset(196, 190),
      Offset(762, 202),
      Offset(805, 408),
      Offset(684, 602),
      Offset(446, 665),
      Offset(205, 575),
      Offset(142, 380),
    ];
    return List<Offset>.generate(
      count,
      (index) => posterPositions[index % posterPositions.length],
    );
  }
}

class _RelationshipOrbitPainter extends CustomPainter {
  const _RelationshipOrbitPainter({
    required this.count,
    required this.positions,
    required this.progress,
    required this.pulse,
  });

  final int count;
  final List<Offset> positions;
  final double progress;
  final double pulse;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final nebula = Paint()
      ..shader = RadialGradient(
        colors: [
          const Color(0x70ffe2ee),
          const Color(0x30e8e0ff),
          Colors.transparent,
        ],
      ).createShader(
        Rect.fromCircle(center: center, radius: 300 + pulse * 18),
      );
    canvas.drawCircle(center, 310 + pulse * 18, nebula);

    for (var ring = 0; ring < 4; ring++) {
      final rect = Rect.fromCenter(
        center: center,
        width: 420 + ring * 118,
        height: 280 + ring * 70,
      );
      canvas.drawOval(
        rect,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = ring == 0 ? 7 : 4
          ..color = const Color(0x16ffffff)
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 9),
      );
      canvas.drawOval(
        rect,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = ring == 0 ? 2 : 1.2
          ..shader = SweepGradient(
            transform: GradientRotation(progress * pi * 2 + ring * 0.5),
            colors: const [
              Color(0x24ad4f7f),
              Color(0x99d8b477),
              Color(0x638970bd),
              Color(0x15ad4f7f),
            ],
          ).createShader(rect),
      );
    }

    final cometPaint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.4
      ..strokeCap = StrokeCap.round
      ..shader = const LinearGradient(
        colors: [
          Colors.transparent,
          Color(0xd7f7d99a),
          Color(0x42c277a0),
          Colors.transparent,
        ],
      ).createShader(Offset.zero & size);
    for (var ring = 0; ring < 4; ring++) {
      final rect = Rect.fromCenter(
        center: center,
        width: 420 + ring * 118,
        height: 280 + ring * 70,
      );
      final start = progress * pi * 2 + ring * 1.35;
      canvas.drawArc(rect, start, 0.34 + ring * 0.03, false, cometPaint);
    }

    if (positions.length > 1) {
      final constellation = Path()
        ..moveTo(positions.first.dx, positions.first.dy);
      for (final point in positions.skip(1)) {
        constellation.lineTo(point.dx, point.dy);
      }
      canvas.drawPath(
        constellation,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.1
          ..shader = const LinearGradient(
            colors: [
              Color(0x25ad4f7f),
              Color(0x70d8b477),
              Color(0x318970bd),
            ],
          ).createShader(Offset.zero & size),
      );
    }

    final starPaint = Paint()..color = const Color(0xa3d8b477);
    for (var index = 0; index < 62; index++) {
      final seed = index * 71.0;
      final x = (seed + progress * 46) % size.width;
      final y =
          (index * 113.0 + sin(progress * pi * 2 + index) * 6) % size.height;
      final radius = index % 9 == 0 ? 2.1 : 0.8 + index % 3 * 0.32;
      canvas.drawCircle(Offset(x, y), radius, starPaint);
    }
  }

  @override
  bool shouldRepaint(covariant _RelationshipOrbitPainter oldDelegate) =>
      oldDelegate.progress != progress ||
      oldDelegate.pulse != pulse ||
      oldDelegate.count != count;
}

class _OrbitHeart extends StatelessWidget {
  const _OrbitHeart();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 190,
      height: 190,
      padding: const EdgeInsets.all(10),
      decoration: const BoxDecoration(
        shape: BoxShape.circle,
        gradient: SweepGradient(
          colors: [
            Color(0xffdcb879),
            Color(0xffb45a85),
            Color(0xff9279c3),
            Color(0xffdcb879),
          ],
        ),
        boxShadow: [
          BoxShadow(
            color: Color(0x43b65d89),
            blurRadius: 55,
            spreadRadius: 13,
          ),
          BoxShadow(
            color: Color(0x48f2d391),
            blurRadius: 30,
            spreadRadius: 2,
          ),
        ],
      ),
      child: Container(
        padding: const EdgeInsets.all(5),
        decoration: const BoxDecoration(
          shape: BoxShape.circle,
          color: Colors.white,
        ),
        child: const ClipOval(
          child: Image(
            image: AssetImage(_avatarAsset),
            fit: BoxFit.cover,
          ),
        ),
      ),
    );
  }
}

class _OrbitNode extends StatelessWidget {
  const _OrbitNode({
    required this.event,
    required this.selected,
    required this.pulse,
    required this.onTap,
  });

  final RelationshipOrbitEvent event;
  final bool selected;
  final double pulse;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final visual = _eventVisual(event.kind);
    final size = selected ? 100.0 : 84.0;
    return Transform.translate(
      offset: Offset(0, sin(pulse * pi * 2 + event.id.hashCode) * 4.6),
      child: GestureDetector(
        onTap: onTap,
        child: SizedBox(
          width: 125,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              AnimatedContainer(
                duration: const Duration(milliseconds: 420),
                curve: Curves.easeOutBack,
                width: size,
                height: size,
                padding: EdgeInsets.all(selected ? 3 : 1.5),
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: selected
                      ? const SweepGradient(
                          colors: [
                            Color(0xffffde9b),
                            Color(0xffc65d91),
                            Color(0xff9a80cd),
                            Color(0xffffde9b),
                          ],
                        )
                      : null,
                  color: selected
                      ? null
                      : (xiaoyouIsDark(context)
                          ? const Color(0xaa27242a)
                          : const Color(0xaaffffff)),
                  boxShadow: [
                    BoxShadow(
                      color:
                          visual.color.withValues(alpha: selected ? 0.42 : 0.2),
                      blurRadius: selected ? 28 : 15,
                      spreadRadius: selected ? 4 : 0,
                    ),
                  ],
                ),
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [
                        Colors.white,
                        visual.color.withValues(alpha: 0.18),
                      ],
                    ),
                    border:
                        Border.all(color: const Color(0xeaffffff), width: 1.6),
                  ),
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      Icon(
                        event.locked ? Icons.lock_rounded : visual.icon,
                        color: visual.color,
                        size: selected ? 38 : 31,
                      ),
                      if (event.newlyUnlocked)
                        const Positioned(
                          right: 5,
                          top: 5,
                          child: Icon(
                            Icons.auto_awesome_rounded,
                            color: _orbitGold,
                            size: 14,
                          ),
                        ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 5),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
                decoration: BoxDecoration(
                  color: xiaoyouIsDark(context)
                      ? const Color(0xe826242a)
                      : const Color(0xe8ffffff),
                  borderRadius: BorderRadius.circular(99),
                  border: Border.all(color: const Color(0xccffffff)),
                ),
                child: Text(
                  _eventLabel(event.kind),
                  maxLines: 1,
                  style: TextStyle(
                    color: selected ? visual.color : _orbitMuted,
                    fontSize: 11.5,
                    fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SelectedMemoryCard extends StatelessWidget {
  const _SelectedMemoryCard({
    super.key,
    required this.event,
    required this.api,
    required this.onTap,
  });

  final RelationshipOrbitEvent event;
  final XiaoyouApi api;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final visual = _eventVisual(event.kind);
    final media = event.message;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.fromLTRB(22, 0, 22, 18),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [
              Color(0xeeffffff),
              Color(0xdffff4f8),
              Color(0xd8f0eaff),
            ],
          ),
          borderRadius: BorderRadius.circular(34),
          border: Border.all(color: const Color(0xfaffffff), width: 1.6),
          boxShadow: const [
            BoxShadow(
              color: Color(0x29ad547f),
              blurRadius: 34,
              offset: Offset(0, 16),
            ),
            BoxShadow(
              color: Color(0x45ffffff),
              blurRadius: 10,
              offset: Offset(-4, -3),
            ),
          ],
        ),
        child: Stack(
          children: [
            Positioned(
              right: -3,
              top: -8,
              child: _GlassBubble(
                size: 43,
                color: visual.color.withValues(alpha: 0.2),
              ),
            ),
            Positioned(
              right: 24,
              bottom: -18,
              child: _GlassBubble(
                size: 26,
                color: _orbitGold.withValues(alpha: 0.18),
              ),
            ),
            Row(
              children: [
                _MemoryPreview(
                  event: event,
                  api: api,
                  color: visual.color,
                  size: 112,
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(0, 6, 4, 4),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Icon(visual.icon, color: visual.color, size: 18),
                            const SizedBox(width: 7),
                            Expanded(
                              child: Text(
                                event.title,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                  color: _orbitInk,
                                  fontSize: 18,
                                  fontWeight: FontWeight.w800,
                                  letterSpacing: 0.3,
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),
                        Text(
                          event.locked ? '这封信还在等待约定的时间' : event.subtitle,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: _orbitMuted,
                            height: 1.42,
                            fontSize: 12.5,
                          ),
                        ),
                        if (media?.kind == 'voice') ...[
                          const SizedBox(height: 9),
                          _MiniVoiceLine(color: visual.color),
                        ],
                        const SizedBox(height: 8),
                        Row(
                          children: [
                            Text(
                              _prettyTime(event.occurredAt),
                              style: const TextStyle(
                                color: Color(0xffb09ba5),
                                fontSize: 11,
                              ),
                            ),
                            const Spacer(),
                            Text(
                              media?.kind == 'voice' ? '重听这一刻' : '展开回忆',
                              style: TextStyle(
                                color: visual.color,
                                fontSize: 11,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            Icon(
                              Icons.chevron_right_rounded,
                              color: visual.color,
                              size: 18,
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _MemoryPreview extends StatelessWidget {
  const _MemoryPreview({
    required this.event,
    required this.api,
    required this.color,
    this.size = 78,
  });

  final RelationshipOrbitEvent event;
  final XiaoyouApi api;
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    final message = event.message;
    ImageProvider<Object>? provider;
    if (message != null && message.localPath.isNotEmpty) {
      final file = File(message.localPath);
      if (file.existsSync()) {
        provider = FileImage(file);
      }
    }
    if (provider == null && message != null && message.mediaId.isNotEmpty) {
      provider = NetworkImage(
        api.mediaUrl(message.mediaId),
        headers: api.mediaHeaders,
      );
    }
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(22),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            color.withValues(alpha: 0.24),
            const Color(0xfffdf6fa),
          ],
        ),
        image: provider == null
            ? null
            : DecorationImage(image: provider, fit: BoxFit.cover),
        boxShadow: [
          BoxShadow(
            color: color.withValues(alpha: 0.18),
            blurRadius: 16,
            offset: const Offset(0, 8),
          ),
        ],
      ),
      child: provider == null
          ? Icon(
              event.locked ? Icons.lock_rounded : _eventVisual(event.kind).icon,
              color: color,
              size: 31,
            )
          : null,
    );
  }
}

class _MiniVoiceLine extends StatelessWidget {
  const _MiniVoiceLine({required this.color});

  final Color color;

  @override
  Widget build(BuildContext context) {
    const bars = <double>[7, 14, 10, 18, 12, 21, 9, 16, 12, 19, 8, 14];
    return Container(
      height: 28,
      padding: const EdgeInsets.symmetric(horizontal: 9),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.55),
        borderRadius: BorderRadius.circular(99),
        border: Border.all(color: Colors.white.withValues(alpha: 0.8)),
      ),
      child: Row(
        children: [
          Icon(Icons.play_arrow_rounded, color: color, size: 18),
          const SizedBox(width: 4),
          for (final height in bars)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 1),
              child: Container(
                width: 2,
                height: height,
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.68),
                  borderRadius: BorderRadius.circular(99),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _GlassBubble extends StatelessWidget {
  const _GlassBubble({required this.size, required this.color});

  final double size;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            Colors.white.withValues(alpha: 0.88),
            color,
            Colors.white.withValues(alpha: 0.18),
          ],
        ),
        border: Border.all(
          color: Colors.white.withValues(alpha: 0.9),
          width: 1.4,
        ),
        boxShadow: [
          BoxShadow(
            color: color.withValues(alpha: 0.26),
            blurRadius: 16,
            spreadRadius: 1,
          ),
        ],
      ),
    );
  }
}

class _HeaderOrbitFlourish extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width * 0.52, size.height * 0.52);
    final orbit = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2
      ..shader = const LinearGradient(
        colors: [
          Colors.transparent,
          Color(0x70c6759a),
          Color(0x98d5b274),
          Colors.transparent,
        ],
      ).createShader(Offset.zero & size);
    canvas.save();
    canvas.translate(center.dx, center.dy);
    canvas.rotate(-0.38);
    canvas.drawOval(
      Rect.fromCenter(
        center: Offset.zero,
        width: size.width * 0.88,
        height: size.height * 0.44,
      ),
      orbit,
    );
    canvas.restore();
    final star = Paint()..color = const Color(0xbdd5b274);
    canvas.drawCircle(Offset(size.width * 0.86, size.height * 0.24), 2.6, star);
    canvas.drawCircle(Offset(size.width * 0.22, size.height * 0.75), 1.7, star);
    canvas.drawCircle(
      Offset(size.width * 0.76, size.height * 0.67),
      1.2,
      star,
    );
  }

  @override
  bool shouldRepaint(covariant _HeaderOrbitFlourish oldDelegate) => false;
}

class _OrbitMemorySheet extends StatelessWidget {
  const _OrbitMemorySheet({required this.event, required this.api});

  final RelationshipOrbitEvent event;
  final XiaoyouApi api;

  @override
  Widget build(BuildContext context) {
    final visual = _eventVisual(event.kind);
    return Material(
      color: const Color(0xfffffbfd),
      borderRadius: const BorderRadius.vertical(top: Radius.circular(34)),
      clipBehavior: Clip.antiAlias,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(22, 12, 22, 30),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 44,
                  height: 5,
                  decoration: BoxDecoration(
                    color: const Color(0xffdfcbd5),
                    borderRadius: BorderRadius.circular(99),
                  ),
                ),
              ),
              const SizedBox(height: 22),
              Row(
                children: [
                  _MemoryPreview(event: event, api: api, color: visual.color),
                  const SizedBox(width: 15),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          event.title,
                          style: const TextStyle(
                            color: _orbitInk,
                            fontWeight: FontWeight.w900,
                            fontSize: 21,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          _prettyTime(event.occurredAt),
                          style: const TextStyle(color: _orbitMuted),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 20),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(18),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(24),
                  border: Border.all(color: const Color(0xffffe7f1)),
                ),
                child: Text(
                  event.subtitle.isEmpty ? '这一刻已经被星河好好保存。' : event.subtitle,
                  style: const TextStyle(
                    color: _orbitInk,
                    fontSize: 16,
                    height: 1.65,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _UniverseBackgroundPainter extends CustomPainter {
  const _UniverseBackgroundPainter(this.progress);

  final double progress;

  @override
  void paint(Canvas canvas, Size size) {
    final background = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [
          Color(0xfffffaf7),
          Color(0xfffff3f8),
          Color(0xfff2edfb),
          Color(0xfffff9fb),
        ],
      ).createShader(Offset.zero & size);
    canvas.drawRect(Offset.zero & size, background);

    final warmGlow = Paint()
      ..shader = const RadialGradient(
        colors: [
          Color(0x99fff7e8),
          Color(0x4fffe0ea),
          Colors.transparent,
        ],
      ).createShader(
        Rect.fromCircle(
          center: Offset(size.width * 0.13, size.height * 0.14),
          radius: size.width * 0.58,
        ),
      );
    canvas.drawRect(Offset.zero & size, warmGlow);

    final lilacGlow = Paint()
      ..shader = const RadialGradient(
        colors: [
          Color(0x61e4d9ff),
          Color(0x2fdcafd4),
          Colors.transparent,
        ],
      ).createShader(
        Rect.fromCircle(
          center: Offset(size.width * 0.88, size.height * 0.47),
          radius: size.width * 0.66,
        ),
      );
    canvas.drawRect(Offset.zero & size, lilacGlow);

    final mist = Paint()..color = const Color(0x3fffffff);
    canvas.drawCircle(
      Offset(size.width * 0.78, 190 + sin(progress * pi * 2) * 16),
      180,
      mist,
    );
    canvas.drawCircle(
      Offset(size.width * 0.12, size.height * 0.63),
      145,
      Paint()..color = const Color(0x26d9cfff),
    );

    final ambientOrbit = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.1
      ..shader = SweepGradient(
        transform: GradientRotation(progress * pi * 2),
        colors: const [
          Colors.transparent,
          Color(0x70e2bd83),
          Color(0x56bc6e99),
          Color(0x36a797cf),
          Colors.transparent,
        ],
      ).createShader(Offset.zero & size);
    canvas.save();
    canvas.translate(size.width * 0.55, size.height * 0.44);
    canvas.rotate(-0.34);
    canvas.drawOval(
      Rect.fromCenter(
        center: Offset.zero,
        width: size.width * 1.45,
        height: size.width * 0.66,
      ),
      ambientOrbit,
    );
    canvas.restore();

    for (var index = 0; index < 54; index++) {
      final phase = index * 0.713 + progress * pi * 2;
      final x = (index * 83.0 + sin(phase) * 13) % size.width;
      final y = (index * 149.0 + cos(phase * 0.82) * 11) % size.height;
      final radius = index % 11 == 0 ? 2.2 : 0.65 + (index % 3) * 0.35;
      final color =
          index.isEven ? const Color(0x90e4bf7b) : const Color(0x82ffffff);
      canvas.drawCircle(Offset(x, y), radius, Paint()..color = color);
    }

    for (var index = 0; index < 7; index++) {
      final bubbleSize = 9.0 + (index % 3) * 6;
      final x =
          size.width == 0 ? 0.0 : (index * 97.0 + progress * 18) % size.width;
      final y =
          (size.height * 0.18 + index * 173.0 - progress * 24) % size.height;
      canvas.drawCircle(
        Offset(x, y),
        bubbleSize,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1
          ..color = const Color(0x54ffffff),
      );
    }
  }

  @override
  bool shouldRepaint(covariant _UniverseBackgroundPainter oldDelegate) =>
      oldDelegate.progress != progress;
}

class _SoftNotice extends StatelessWidget {
  const _SoftNotice({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
      decoration: BoxDecoration(
        color: const Color(0xbffff9fc),
        borderRadius: BorderRadius.circular(99),
        border: Border.all(color: const Color(0xcfffffff)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.cloud_off_rounded, color: _orbitRose, size: 14),
          const SizedBox(width: 6),
          Text(
            text,
            style: const TextStyle(
              color: _orbitMuted,
              fontSize: 10.5,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

class _GlassIconButton extends StatelessWidget {
  const _GlassIconButton({
    required this.icon,
    required this.tooltip,
    required this.onPressed,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: IconButton(
        onPressed: onPressed,
        icon: Icon(icon, size: 19),
        style: IconButton.styleFrom(
          foregroundColor: _orbitRose,
          backgroundColor: const Color(0xd9ffffff),
          side: const BorderSide(color: Color(0xffffffff)),
        ),
      ),
    );
  }
}

_EventVisual _eventVisual(RelationshipEventKind kind) => switch (kind) {
      RelationshipEventKind.firstLight => const _EventVisual(
          Icons.favorite_rounded,
          Color(0xffc55585),
        ),
      RelationshipEventKind.anniversary => const _EventVisual(
          Icons.auto_awesome_rounded,
          Color(0xffd09d50),
        ),
      RelationshipEventKind.photo => const _EventVisual(
          Icons.photo_library_rounded,
          Color(0xffa56fbd),
        ),
      RelationshipEventKind.conversation => const _EventVisual(
          Icons.format_quote_rounded,
          Color(0xffb45683),
        ),
      RelationshipEventKind.journal => const _EventVisual(
          Icons.auto_stories_rounded,
          Color(0xffae6b91),
        ),
      RelationshipEventKind.capsule => const _EventVisual(
          Icons.mark_email_unread_rounded,
          Color(0xffd0a151),
        ),
      RelationshipEventKind.voiceMemory => const _EventVisual(
          Icons.spatial_audio_off_rounded,
          Color(0xff8170b8),
        ),
      RelationshipEventKind.achievement => const _EventVisual(
          Icons.workspace_premium_rounded,
          Color(0xffc17a52),
        ),
    };

String _eventLabel(RelationshipEventKind kind) => switch (kind) {
      RelationshipEventKind.firstLight => '第一次',
      RelationshipEventKind.anniversary => '纪念日',
      RelationshipEventKind.photo => '重要照片',
      RelationshipEventKind.conversation => '难忘对话',
      RelationshipEventKind.journal => '日记',
      RelationshipEventKind.capsule => '时光信笺',
      RelationshipEventKind.voiceMemory => '语音房间',
      RelationshipEventKind.achievement => '新成就',
    };

class _EventVisual {
  const _EventVisual(this.icon, this.color);

  final IconData icon;
  final Color color;
}

String _prettyTime(DateTime value) {
  final now = DateTime.now();
  if (now.year == value.year &&
      now.month == value.month &&
      now.day == value.day) {
    return '今天 ${value.hour.toString().padLeft(2, '0')}:'
        '${value.minute.toString().padLeft(2, '0')}';
  }
  return '${value.year}.${value.month.toString().padLeft(2, '0')}.'
      '${value.day.toString().padLeft(2, '0')}';
}

extension _FirstOrNull<T> on Iterable<T> {
  T? get firstOrNull => isEmpty ? null : first;
}

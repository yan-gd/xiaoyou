import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'xiaoyou_api.dart';
import 'theme_controller.dart';

enum FirstRunOnboardingResult { completed, skipped }

const _page = Color(0xfffffdfc);
const _ink = Color(0xff211f20);
const _secondary = Color(0xff777174);
const _hairline = Color(0xffebe7e8);

/// Matches the native Android launch background so cold-start never exposes
/// the conversation before session restoration has resolved.
class FirstRunAppBootSurface extends StatelessWidget {
  const FirstRunAppBootSurface({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(backgroundColor: xiaoyouPageSurface(context));
  }
}

/// Shown immediately after a fresh login while the account/profile handshake is
/// completing. It is intentionally the first frame of the real welcome
/// animation, so there is no visible "connecting" page between login and setup.
class FirstRunOnboardingHandoff extends StatelessWidget {
  const FirstRunOnboardingHandoff({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(backgroundColor: xiaoyouPageSurface(context));
  }
}

class FirstRunOnboardingScreen extends StatefulWidget {
  const FirstRunOnboardingScreen({
    required this.api,
    required this.profile,
    super.key,
  });

  final XiaoyouApi api;
  final XiaoyouUserProfile profile;

  @override
  State<FirstRunOnboardingScreen> createState() =>
      _FirstRunOnboardingScreenState();
}

class _FirstRunOnboardingScreenState extends State<FirstRunOnboardingScreen>
    with TickerProviderStateMixin {
  late final TextEditingController _name;
  late final TextEditingController _about;
  late final AnimationController _welcomeController;

  DateTime? _birthday;
  final Set<String> _traits = <String>{};
  String _lastTrait = '';
  int _stage = 0;
  int _transitionDirection = 1;
  bool _saving = false;
  String _error = '';

  static const _traitOptions = <String>[
    '慢热一点',
    '爱分享日常',
    '喜欢直接沟通',
    '希望被温柔提醒',
    '夜猫子',
    '容易想很多',
  ];

  @override
  void initState() {
    super.initState();
    _name = TextEditingController(text: widget.profile.displayName.trim());
    _about = TextEditingController(text: widget.profile.aboutMe.trim());
    _birthday = DateTime.tryParse(widget.profile.birthday);
    _name.addListener(_refreshPreview);
    _about.addListener(_refreshPreview);
    _welcomeController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1480),
    )..forward();
  }

  @override
  void dispose() {
    _name
      ..removeListener(_refreshPreview)
      ..dispose();
    _about
      ..removeListener(_refreshPreview)
      ..dispose();
    _welcomeController.dispose();
    super.dispose();
  }

  void _refreshPreview() {
    if (mounted) {
      setState(() {});
    }
  }

  String get _aboutForServer {
    final parts = <String>[
      if (_traits.isNotEmpty) '我更像：${_traits.join('、')}',
      if (_about.text.trim().isNotEmpty) _about.text.trim(),
    ];
    return parts.join('\n');
  }

  String get _conversationPrompt {
    if (_stage == 1) {
      return _name.text.trim().isEmpty ? '以后你会怎么叫我？' : '你会怎么叫我？';
    }
    if (_stage == 2) {
      return _birthday == null ? '生日一定要告诉你吗？' : '你会记得这一天吗？';
    }
    return '今天有点累。';
  }

  String get _conversationReply {
    if (_stage == 1) {
      final value = _name.text.trim();
      return value.isEmpty ? '告诉我一个你喜欢的称呼，以后聊天我就这样叫你。' : '好。以后我就叫你「$value」。';
    }
    if (_stage == 2) {
      final value = _birthday;
      return value == null
          ? '不用。你愿意说的，我才会记住。'
          : '${value.month} 月 ${value.day} 日。记住了，到那天我会早点来。';
    }
    switch (_lastTrait) {
      case '慢热一点':
        return '那我们慢一点。你不用一次把自己说完。';
      case '爱分享日常':
        return '小事也可以讲给我听，我会把它们当作你的日常。';
      case '喜欢直接沟通':
        return '明白。以后我会少绕弯，把重点说清楚。';
      case '希望被温柔提醒':
        return '好。我会轻一点提醒你，不催你，也不给你压力。';
      case '夜猫子':
        return '深夜想说话也可以，我会跟着你的节奏。';
      case '容易想很多':
        return '那我先陪你把事情理清，不急着替你下结论。';
      default:
        if (_about.text.trim().isNotEmpty) {
          return '我记下了。之后的聊天里，我会慢慢理解这句话。';
        }
        return '没关系。剩下的，我们可以在聊天里慢慢认识。';
    }
  }

  Future<void> _skip() async {
    if (_saving) {
      return;
    }
    HapticFeedback.selectionClick();
    if (mounted) {
      Navigator.of(context).pop(FirstRunOnboardingResult.skipped);
    }
  }

  void _next() {
    FocusManager.instance.primaryFocus?.unfocus();
    if (_stage == 1) {
      final name = _name.text.trim();
      if (name.isEmpty || name.length > 32) {
        setState(() => _error = '告诉小悠一个 1–32 个字的称呼吧');
        return;
      }
    }
    setState(() {
      _error = '';
      _transitionDirection = 1;
      _stage = math.min(3, _stage + 1);
    });
    HapticFeedback.selectionClick();
  }

  void _back() {
    if (_saving || _stage <= 0) {
      return;
    }
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      _error = '';
      _transitionDirection = -1;
      _stage -= 1;
    });
    HapticFeedback.selectionClick();
  }

  Future<void> _pickBirthday() async {
    final now = DateTime.now();
    final value = await showDatePicker(
      context: context,
      initialDate: _birthday ?? DateTime(now.year - 20, now.month, now.day),
      firstDate: DateTime(1900),
      lastDate: now,
      helpText: '选择生日',
      cancelText: '先不填',
      confirmText: '完成',
    );
    if (value != null && mounted) {
      setState(() => _birthday = value);
      HapticFeedback.selectionClick();
    }
  }

  void _toggleTrait(String trait, bool selected) {
    setState(() {
      if (selected) {
        _traits.add(trait);
        _lastTrait = trait;
      } else {
        _traits.remove(trait);
        if (_lastTrait == trait) {
          _lastTrait = _traits.isEmpty ? '' : _traits.last;
        }
      }
    });
    HapticFeedback.selectionClick();
  }

  Future<void> _finish() async {
    if (_saving) {
      return;
    }
    final name = _name.text.trim();
    if (name.isEmpty || name.length > 32) {
      setState(() {
        _transitionDirection = -1;
        _stage = 1;
        _error = '告诉小悠一个 1–32 个字的称呼吧';
      });
      return;
    }
    setState(() {
      _saving = true;
      _error = '';
    });
    try {
      final birthday = _birthday == null
          ? ''
          : '${_birthday!.year.toString().padLeft(4, '0')}-'
              '${_birthday!.month.toString().padLeft(2, '0')}-'
              '${_birthday!.day.toString().padLeft(2, '0')}';
      await widget.api.updateAccountProfile(
        displayName: name,
        birthday: birthday,
        aboutMe: _aboutForServer,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _transitionDirection = 1;
        _stage = 4;
        _saving = false;
      });
      HapticFeedback.mediumImpact();
    } catch (_) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = '暂时保存不了。你可以重试，也可以先跳过。';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final keyboard = MediaQuery.viewInsetsOf(context).bottom;
    return PopScope(
      canPop: false,
      child: Scaffold(
        resizeToAvoidBottomInset: false,
        backgroundColor: xiaoyouPageSurface(context),
        body: AnimatedPadding(
          padding: EdgeInsets.only(bottom: keyboard),
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOutCubic,
          child: Stack(
            children: [
              Positioned.fill(
                child: AnimatedSwitcher(
                  duration: const Duration(milliseconds: 500),
                  child: _QuietBackdrop(
                    key: ValueKey('background-${_stage.clamp(0, 3)}'),
                    stage: _stage.clamp(0, 3).toInt(),
                  ),
                ),
              ),
              SafeArea(
                child: Column(
                  children: [
                    _TopBar(
                      stage: _stage,
                      busy: _saving,
                      onBack: _back,
                      onSkip: _skip,
                    ),
                    Expanded(
                      child: ClipRect(
                        child: AnimatedSwitcher(
                          duration: const Duration(milliseconds: 560),
                          reverseDuration: const Duration(milliseconds: 360),
                          switchInCurve: Curves.easeOutQuart,
                          switchOutCurve: Curves.easeInCubic,
                          transitionBuilder: (child, animation) {
                            final direction = _transitionDirection.toDouble();
                            final slide = Tween<Offset>(
                              begin: Offset(0.035 * direction, 0.012),
                              end: Offset.zero,
                            ).animate(
                              CurvedAnimation(
                                parent: animation,
                                curve: Curves.easeOutCubic,
                                reverseCurve: Curves.easeInCubic,
                              ),
                            );
                            final scale = Tween<double>(
                              begin: 0.992,
                              end: 1,
                            ).animate(
                              CurvedAnimation(
                                parent: animation,
                                curve: Curves.easeOutCubic,
                              ),
                            );
                            return FadeTransition(
                              opacity: animation,
                              child: SlideTransition(
                                position: slide,
                                child:
                                    ScaleTransition(scale: scale, child: child),
                              ),
                            );
                          },
                          child: switch (_stage) {
                            0 => _buildWelcome(),
                            1 => _buildNameStep(),
                            2 => _buildBirthdayStep(),
                            3 => _buildPersonalityStep(),
                            _ => _buildRecognition(),
                          },
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildWelcome() {
    final subtitleOpacity = CurvedAnimation(
      parent: _welcomeController,
      curve: const Interval(0.46, 0.78, curve: Curves.easeOutQuart),
    );
    final subtitleSlide = Tween<Offset>(
      begin: const Offset(0, 0.12),
      end: Offset.zero,
    ).animate(
      CurvedAnimation(
        parent: _welcomeController,
        curve: const Interval(0.44, 0.80, curve: Curves.easeOutQuart),
      ),
    );
    final actionOpacity = CurvedAnimation(
      parent: _welcomeController,
      curve: const Interval(0.68, 0.96, curve: Curves.easeOutQuart),
    );
    final actionScale = Tween<double>(begin: 0.97, end: 1).animate(
      CurvedAnimation(
        parent: _welcomeController,
        curve: const Interval(0.68, 1, curve: Curves.easeOutBack),
      ),
    );

    return LayoutBuilder(
      key: const ValueKey('welcome'),
      builder: (context, constraints) {
        final compact = constraints.maxHeight < 620;
        return Padding(
          padding: const EdgeInsets.fromLTRB(26, 0, 26, 18),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 460),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  _KineticTextEntrance(
                    animation: _welcomeController,
                    text: '欢迎来到小悠',
                    fontSize: 36,
                    letterSpacing: -1.35,
                  ),
                  const SizedBox(height: 18),
                  FadeTransition(
                    opacity: subtitleOpacity,
                    child: SlideTransition(
                      position: subtitleSlide,
                      child: Text(
                        '先让我认识一点点你。\n剩下的，我们以后慢慢聊。',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontSize: compact ? 15 : 16,
                          height: 1.55,
                          letterSpacing: -0.15,
                          color: _secondary,
                        ),
                      ),
                    ),
                  ),
                  SizedBox(height: compact ? 42 : 56),
                  FadeTransition(
                    opacity: actionOpacity,
                    child: ScaleTransition(
                      scale: actionScale,
                      child: _PrimaryButton(
                        label: '继续',
                        onPressed: _next,
                        compact: true,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _buildNameStep() {
    return _StepScaffold(
      key: const ValueKey('name'),
      eyebrow: '01',
      title: '我该怎么叫你？',
      subtitle: '这个称呼，会自然地出现在之后的聊天里。',
      preview: _LiveConversationPreview(
        prompt: _conversationPrompt,
        reply: _conversationReply,
      ),
      error: _error,
      body: TextField(
        controller: _name,
        autofocus: false,
        textInputAction: TextInputAction.done,
        maxLength: 32,
        onSubmitted: (_) => _next(),
        decoration: _inputDecoration(
          label: '你的称呼',
          hint: '例如：YOYO',
        ),
      ),
      footer: _PrimaryButton(label: '继续', onPressed: _next),
    );
  }

  Widget _buildBirthdayStep() {
    final value = _birthday;
    return _StepScaffold(
      key: const ValueKey('birthday'),
      eyebrow: '02',
      title: '有一天，值得我记住吗？',
      subtitle: '生日是选填的。你愿意告诉我的，我才会记住。',
      preview: _LiveConversationPreview(
        prompt: _conversationPrompt,
        reply: _conversationReply,
      ),
      error: _error,
      body: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          _SelectionCard(
            title: value == null
                ? '选择生日'
                : '${value.year} 年 ${value.month} 月 ${value.day} 日',
            subtitle: value == null ? '不填写也没关系' : '我会记住这一天',
            selected: value != null,
            onTap: _pickBirthday,
          ),
          if (value != null) ...[
            const SizedBox(height: 4),
            TextButton(
              onPressed: () => setState(() => _birthday = null),
              style: TextButton.styleFrom(
                foregroundColor: _secondary,
                visualDensity: VisualDensity.compact,
              ),
              child: const Text('清除'),
            ),
          ],
        ],
      ),
      footer: _PrimaryButton(label: '继续', onPressed: _next),
    );
  }

  Widget _buildPersonalityStep() {
    return _StepScaffold(
      key: const ValueKey('personality'),
      eyebrow: '03',
      title: '你喜欢怎样被理解？',
      subtitle: '点一下，看看它会怎样改变我们之后的对话。',
      preview: _LiveConversationPreview(
        prompt: _conversationPrompt,
        reply: _conversationReply,
      ),
      error: _error,
      body: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            spacing: 7,
            runSpacing: 7,
            children: _traitOptions.map((trait) {
              final selected = _traits.contains(trait);
              return _TraitChip(
                label: trait,
                selected: selected,
                onSelected: (value) => _toggleTrait(trait, value),
              );
            }).toList(),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _about,
            minLines: 1,
            maxLines: 1,
            maxLength: 160,
            textInputAction: TextInputAction.done,
            decoration: _inputDecoration(
              label: '还想补充一句？（选填）',
              hint: '例如：我忙的时候会安静一阵',
            ),
          ),
        ],
      ),
      footer: _PrimaryButton(
        label: _saving ? '正在记住…' : '完成',
        busy: _saving,
        onPressed: _saving ? null : _finish,
      ),
    );
  }

  Widget _buildRecognition() {
    return _RecognitionPage(
      key: const ValueKey('recognition'),
      onEnter: () {
        HapticFeedback.lightImpact();
        Navigator.of(context).pop(FirstRunOnboardingResult.completed);
      },
    );
  }

  InputDecoration _inputDecoration({
    required String label,
    required String hint,
  }) {
    return InputDecoration(
      labelText: label,
      hintText: hint,
      filled: true,
      fillColor: Colors.white.withValues(alpha: 0.94),
      counterText: '',
      contentPadding: const EdgeInsets.symmetric(horizontal: 17, vertical: 15),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(17),
        borderSide: const BorderSide(color: _hairline),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(17),
        borderSide: const BorderSide(color: _hairline),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(17),
        borderSide: const BorderSide(color: Color(0xff9a8bc3), width: 1.1),
      ),
    );
  }
}

class _KineticTextEntrance extends StatelessWidget {
  const _KineticTextEntrance({
    required this.animation,
    required this.text,
    required this.fontSize,
    required this.letterSpacing,
  });

  final Animation<double> animation;
  final String text;
  final double fontSize;
  final double letterSpacing;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: animation,
      builder: (context, _) {
        return Wrap(
          alignment: WrapAlignment.center,
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 0,
          runSpacing: 0,
          children: List.generate(text.length, (index) {
            final start = 0.035 + index * 0.050;
            final end = (start + 0.40).clamp(0.0, 0.94).toDouble();
            final raw = ((animation.value - start) / (end - start))
                .clamp(0.0, 1.0)
                .toDouble();
            final opacity = Curves.easeOutQuart.transform(raw);
            final spring = 1 - math.exp(-6.8 * raw) * math.cos(9.2 * raw);
            final settled = spring.clamp(0.0, 1.065).toDouble();
            final y = 22.0 * (1 - settled);
            final scale = 0.945 + 0.055 * settled;
            return Opacity(
              opacity: opacity,
              child: Transform.translate(
                offset: Offset(0, y),
                child: Transform.scale(
                  scale: scale,
                  alignment: Alignment.bottomCenter,
                  child: Text(
                    text[index],
                    style: TextStyle(
                      fontFamily: 'serif',
                      fontFamilyFallback: [
                        'Noto Serif CJK SC',
                        'Noto Serif SC',
                        'serif'
                      ],
                      fontSize: fontSize,
                      height: 1.10,
                      letterSpacing: letterSpacing * 0.55,
                      fontWeight: FontWeight.w600,
                      color: _ink,
                    ),
                  ),
                ),
              ),
            );
          }),
        );
      },
    );
  }
}

class _EntrancePiece extends StatelessWidget {
  const _EntrancePiece({
    required this.animation,
    required this.interval,
    required this.child,
    this.offsetY = 16,
    this.beginScale = 0.985,
  });

  final Animation<double> animation;
  final Interval interval;
  final Widget child;
  final double offsetY;
  final double beginScale;

  @override
  Widget build(BuildContext context) {
    final curved = CurvedAnimation(
      parent: animation,
      curve: Interval(interval.begin, interval.end, curve: Curves.easeOutQuart),
    );
    final springScale = TweenSequence<double>([
      TweenSequenceItem(
        tween: Tween<double>(begin: beginScale, end: 1.006)
            .chain(CurveTween(curve: Curves.easeOutCubic)),
        weight: 72,
      ),
      TweenSequenceItem(
        tween: Tween<double>(begin: 1.006, end: 1),
        weight: 28,
      ),
    ]).animate(curved);
    final slide = Tween<Offset>(
      begin: Offset(0, offsetY / 100),
      end: Offset.zero,
    ).animate(curved);
    return FadeTransition(
      opacity: curved,
      child: SlideTransition(
        position: slide,
        child: ScaleTransition(scale: springScale, child: child),
      ),
    );
  }
}

class _RecognitionPage extends StatefulWidget {
  const _RecognitionPage({required this.onEnter, super.key});

  final VoidCallback onEnter;

  @override
  State<_RecognitionPage> createState() => _RecognitionPageState();
}

class _RecognitionPageState extends State<_RecognitionPage>
    with TickerProviderStateMixin {
  late final AnimationController _entrance;
  late final AnimationController _hint;

  @override
  void initState() {
    super.initState();
    _entrance = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1380),
    )..forward();
    _hint = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
      lowerBound: 0,
      upperBound: 1,
    );
    Future<void>.delayed(const Duration(milliseconds: 980), () {
      if (mounted) {
        _hint.repeat(reverse: true);
      }
    });
  }

  @override
  void dispose() {
    _entrance.dispose();
    _hint.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final hintIn = CurvedAnimation(
      parent: _entrance,
      curve: const Interval(0.52, 0.90, curve: Curves.easeOutQuart),
    );
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: widget.onEnter,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(28, 0, 28, 54),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              _KineticTextEntrance(
                animation: _entrance,
                text: '我认识你了',
                fontSize: 36,
                letterSpacing: -1.4,
              ),
              const SizedBox(height: 16),
              FadeTransition(
                opacity: hintIn,
                child: AnimatedBuilder(
                  animation: _hint,
                  builder: (context, child) {
                    final breathe = Curves.easeInOut.transform(_hint.value);
                    return Transform.translate(
                      offset: Offset(0, 1.6 * breathe),
                      child: Opacity(
                        opacity: 0.76 + 0.24 * (1 - breathe),
                        child: child,
                      ),
                    );
                  },
                  child: const Text(
                    '点击进入小悠',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: 14,
                      height: 1.4,
                      letterSpacing: 0.2,
                      fontWeight: FontWeight.w400,
                      color: _secondary,
                    ),
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

class _TopBar extends StatelessWidget {
  const _TopBar({
    required this.stage,
    required this.busy,
    required this.onBack,
    required this.onSkip,
  });

  final int stage;
  final bool busy;
  final VoidCallback onBack;
  final VoidCallback onSkip;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 54,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16),
        child: Row(
          children: [
            SizedBox(
              width: 64,
              child: stage > 0 && stage < 4
                  ? Align(
                      alignment: Alignment.centerLeft,
                      child: IconButton(
                        tooltip: '上一步',
                        onPressed: busy ? null : onBack,
                        visualDensity: VisualDensity.compact,
                        icon: const Icon(
                          Icons.arrow_back_ios_new_rounded,
                          size: 18,
                          color: _ink,
                        ),
                      ),
                    )
                  : null,
            ),
            Expanded(
              child: stage >= 1 && stage <= 3
                  ? _ProgressDots(active: stage - 1)
                  : const SizedBox.shrink(),
            ),
            SizedBox(
              width: 64,
              child: stage < 4
                  ? Align(
                      alignment: Alignment.centerRight,
                      child: TextButton(
                        onPressed: busy ? null : onSkip,
                        style: TextButton.styleFrom(
                          foregroundColor: _secondary,
                          padding: const EdgeInsets.symmetric(horizontal: 8),
                        ),
                        child: const Text(
                          '跳过',
                          style: TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),
                    )
                  : null,
            ),
          ],
        ),
      ),
    );
  }
}

class _ProgressDots extends StatelessWidget {
  const _ProgressDots({required this.active});

  final int active;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: List.generate(3, (index) {
        final selected = index == active;
        final passed = index < active;
        return AnimatedContainer(
          duration: const Duration(milliseconds: 360),
          curve: Curves.easeOutCubic,
          width: selected ? 18 : 5,
          height: 5,
          margin: const EdgeInsets.symmetric(horizontal: 3.5),
          decoration: BoxDecoration(
            color: selected
                ? _ink
                : passed
                    ? const Color(0xffaaa3a6)
                    : const Color(0xffdedadb),
            borderRadius: BorderRadius.circular(99),
          ),
        );
      }),
    );
  }
}

class _StepScaffold extends StatefulWidget {
  const _StepScaffold({
    required super.key,
    required this.eyebrow,
    required this.title,
    required this.subtitle,
    required this.preview,
    required this.body,
    required this.footer,
    required this.error,
  });

  final String eyebrow;
  final String title;
  final String subtitle;
  final Widget preview;
  final Widget body;
  final Widget footer;
  final String error;

  @override
  State<_StepScaffold> createState() => _StepScaffoldState();
}

class _StepScaffoldState extends State<_StepScaffold>
    with SingleTickerProviderStateMixin {
  late final AnimationController _entrance;

  @override
  void initState() {
    super.initState();
    _entrance = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 920),
    )..forward();
  }

  @override
  void dispose() {
    _entrance.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = constraints.maxHeight < 650;
        final veryCompact = constraints.maxHeight < 560;
        return Padding(
          padding: EdgeInsets.fromLTRB(
            24,
            compact ? 4 : 12,
            24,
            compact ? 12 : 20,
          ),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 520),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _EntrancePiece(
                    animation: _entrance,
                    interval: const Interval(0.00, 0.38),
                    offsetY: 10,
                    child: Text(
                      widget.eyebrow,
                      style: const TextStyle(
                        fontSize: 12,
                        height: 1,
                        letterSpacing: 1.5,
                        fontWeight: FontWeight.w600,
                        color: Color(0xffaaa3a6),
                      ),
                    ),
                  ),
                  SizedBox(height: compact ? 9 : 13),
                  _EntrancePiece(
                    animation: _entrance,
                    interval: const Interval(0.07, 0.48),
                    offsetY: 17,
                    beginScale: 0.978,
                    child: Text(
                      widget.title,
                      maxLines: 2,
                      style: TextStyle(
                        fontFamily: 'serif',
                        fontFamilyFallback: [
                          'Noto Serif CJK SC',
                          'Noto Serif SC',
                          'serif'
                        ],
                        fontSize: compact ? 27 : 31,
                        height: 1.12,
                        letterSpacing: -0.25,
                        fontWeight: FontWeight.w600,
                        color: _ink,
                      ),
                    ),
                  ),
                  if (!veryCompact) ...[
                    SizedBox(height: compact ? 7 : 10),
                    _EntrancePiece(
                      animation: _entrance,
                      interval: const Interval(0.16, 0.56),
                      offsetY: 13,
                      child: Text(
                        widget.subtitle,
                        maxLines: 2,
                        style: TextStyle(
                          fontSize: compact ? 13.5 : 14.5,
                          height: 1.45,
                          color: _secondary,
                        ),
                      ),
                    ),
                  ],
                  SizedBox(height: compact ? 14 : 20),
                  _EntrancePiece(
                    animation: _entrance,
                    interval: const Interval(0.25, 0.68),
                    offsetY: 18,
                    beginScale: 0.972,
                    child: SizedBox(
                      height: compact ? 116 : 132,
                      child: widget.preview,
                    ),
                  ),
                  SizedBox(height: compact ? 14 : 20),
                  Flexible(
                    fit: FlexFit.tight,
                    child: _EntrancePiece(
                      animation: _entrance,
                      interval: const Interval(0.36, 0.78),
                      offsetY: 18,
                      child: Align(
                        alignment: Alignment.topCenter,
                        child: widget.body,
                      ),
                    ),
                  ),
                  if (widget.error.isNotEmpty) ...[
                    const SizedBox(height: 5),
                    Text(
                      widget.error,
                      textAlign: TextAlign.center,
                      maxLines: 2,
                      style: const TextStyle(
                        color: Color(0xffb44f6c),
                        fontSize: 12.5,
                      ),
                    ),
                  ],
                  const SizedBox(height: 9),
                  _EntrancePiece(
                    animation: _entrance,
                    interval: const Interval(0.52, 0.94),
                    offsetY: 14,
                    beginScale: 0.975,
                    child: Align(
                      alignment: Alignment.center,
                      child: widget.footer,
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class _LiveConversationPreview extends StatelessWidget {
  const _LiveConversationPreview({
    required this.prompt,
    required this.reply,
  });

  final String prompt;
  final String reply;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(
        color: xiaoyouCardSurface(context),
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: xiaoyouHairline(context)),
        boxShadow: const [
          BoxShadow(
            color: Color(0x0a24191f),
            blurRadius: 24,
            offset: Offset(0, 10),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Row(
            children: [
              _MiniAvatar(),
              SizedBox(width: 8),
              Text(
                '未来对话',
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                  color: Color(0xff817a7d),
                ),
              ),
            ],
          ),
          const Spacer(),
          Align(
            alignment: Alignment.centerRight,
            child: Container(
              constraints: const BoxConstraints(maxWidth: 230),
              padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 6),
              decoration: BoxDecoration(
                color: xiaoyouSoftSurface(context),
                borderRadius: BorderRadius.circular(13),
              ),
              child: Text(
                prompt,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12.3, color: _secondary),
              ),
            ),
          ),
          const SizedBox(height: 6),
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 260),
            switchInCurve: Curves.easeOutCubic,
            transitionBuilder: (child, animation) => FadeTransition(
              opacity: animation,
              child: SlideTransition(
                position: Tween<Offset>(
                  begin: const Offset(0, 0.08),
                  end: Offset.zero,
                ).animate(animation),
                child: child,
              ),
            ),
            child: Text(
              reply,
              key: ValueKey(reply),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontSize: 13.5,
                height: 1.36,
                fontWeight: FontWeight.w500,
                color: _ink,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _MiniAvatar extends StatelessWidget {
  const _MiniAvatar();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 25,
      height: 25,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white, width: 1.5),
        boxShadow: const [
          BoxShadow(color: Color(0x123b2b34), blurRadius: 8),
        ],
        image: const DecorationImage(
          image: AssetImage('assets/xiaoyou-avatar.png'),
          fit: BoxFit.cover,
        ),
      ),
    );
  }
}

class _TraitChip extends StatelessWidget {
  const _TraitChip({
    required this.label,
    required this.selected,
    required this.onSelected,
  });

  final String label;
  final bool selected;
  final ValueChanged<bool> onSelected;

  @override
  Widget build(BuildContext context) {
    return FilterChip(
      label: Text(label),
      selected: selected,
      onSelected: onSelected,
      showCheckmark: false,
      side: BorderSide(
        color: selected ? const Color(0xffa89bc8) : const Color(0xffe4e0e1),
      ),
      backgroundColor: xiaoyouCardSurface(context),
      selectedColor: xiaoyouIsDark(context)
          ? const Color(0xff352e3d)
          : const Color(0xfff0ecf8),
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
      labelStyle: TextStyle(
        fontSize: 13,
        fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
        color: selected ? const Color(0xff4e4265) : const Color(0xff615b5e),
      ),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(13)),
    );
  }
}

class _SelectionCard extends StatelessWidget {
  const _SelectionCard({
    required this.title,
    required this.subtitle,
    required this.selected,
    required this.onTap,
  });

  final String title;
  final String subtitle;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: xiaoyouCardSurface(context),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: BorderSide(
          color: selected ? const Color(0xffa89bc8) : xiaoyouHairline(context),
        ),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(18),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(17, 14, 14, 14),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                        color: _ink,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      subtitle,
                      style: const TextStyle(fontSize: 12.5, color: _secondary),
                    ),
                  ],
                ),
              ),
              const Icon(
                Icons.chevron_right_rounded,
                size: 21,
                color: Color(0xffaaa3a6),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _PrimaryButton extends StatefulWidget {
  const _PrimaryButton({
    required this.label,
    required this.onPressed,
    this.busy = false,
    this.compact = false,
  });

  final String label;
  final VoidCallback? onPressed;
  final bool busy;
  final bool compact;

  @override
  State<_PrimaryButton> createState() => _PrimaryButtonState();
}

class _PrimaryButtonState extends State<_PrimaryButton> {
  bool _pressed = false;

  void _setPressed(bool value) {
    if (_pressed != value && mounted) {
      setState(() => _pressed = value);
    }
  }

  @override
  Widget build(BuildContext context) {
    final enabled = widget.onPressed != null;
    return AnimatedScale(
      scale: _pressed ? 0.975 : 1,
      duration: const Duration(milliseconds: 120),
      curve: Curves.easeOutCubic,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: enabled ? (_) => _setPressed(true) : null,
        onTapCancel: enabled ? () => _setPressed(false) : null,
        onTapUp: enabled
            ? (_) {
                _setPressed(false);
                widget.onPressed?.call();
              }
            : null,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          width: widget.compact ? 158 : 190,
          height: 49,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: enabled ? const Color(0xff282527) : const Color(0xffc9c5c7),
            borderRadius: BorderRadius.circular(999),
            boxShadow: enabled
                ? const [
                    BoxShadow(
                      color: Color(0x18251b20),
                      blurRadius: 18,
                      offset: Offset(0, 8),
                    ),
                  ]
                : const [],
          ),
          child: widget.busy
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                    strokeWidth: 1.8,
                    color: Colors.white,
                  ),
                )
              : Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      widget.label,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 14.5,
                        letterSpacing: -0.1,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    if (widget.compact) ...[
                      const SizedBox(width: 7),
                      const Icon(
                        Icons.arrow_forward_rounded,
                        color: Colors.white,
                        size: 17,
                      ),
                    ],
                  ],
                ),
        ),
      ),
    );
  }
}

/// Very quiet background: nearly-white, with only a low-amplitude tint that
/// shifts between steps. No decorative planets/rings compete with the content.
class _QuietBackdrop extends StatelessWidget {
  const _QuietBackdrop({required this.stage, super.key});

  final int stage;

  @override
  Widget build(BuildContext context) {
    final tint = switch (stage) {
      1 => const Color(0xfffbf8fa),
      2 => const Color(0xfffbfaf7),
      3 => const Color(0xfff9f8fc),
      _ => _page,
    };
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [_page, tint, _page],
          stops: const [0, 0.52, 1],
        ),
      ),
    );
  }
}

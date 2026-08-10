import 'dart:io';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

const xiaoyouPrivacyPolicyUrl = 'https://xiaoyou.yoyoyan.cn/privacy';
const xiaoyouUserAgreementUrl = 'https://xiaoyou.yoyoyan.cn/terms';
const xiaoyouIcpQueryUrl = 'https://beian.miit.gov.cn/';
const xiaoyouAppFilingNumber = '渝ICP备2026017342号-2A';

const _privacyConsentVersion = '2026-08-08-user-profile';
const _privacyConsentPreference = 'xiaoyou_privacy_consent_version';
const _systemChannel = MethodChannel('com.yoyo.xiaoyou/system');


bool _xiaoyouFirstFrameDeferred = false;
bool _xiaoyouFirstFrameReleased = false;

void deferXiaoyouFirstFrame() {
  if (_xiaoyouFirstFrameDeferred || _xiaoyouFirstFrameReleased) return;
  _xiaoyouFirstFrameDeferred = true;
  WidgetsBinding.instance.deferFirstFrame();
}

void releaseXiaoyouFirstFrame() {
  if (!_xiaoyouFirstFrameDeferred || _xiaoyouFirstFrameReleased) return;
  _xiaoyouFirstFrameReleased = true;
  WidgetsBinding.instance.allowFirstFrame();
}
const _loginBackgroundAsset = 'assets/login_background.png';
const _violet = Color(0xff8c82f4);
const _violetDeep = Color(0xff7568ef);
const _ink = Color(0xff25243a);
const _muted = Color(0xff8e8ba2);

Future<void> openLegalUrl(String url) async {
  final uri = Uri.tryParse(url);
  if (uri == null ||
      (uri.scheme != 'https' && uri.scheme != 'http') ||
      uri.host.isEmpty) {
    throw const FormatException('Invalid legal document URL');
  }
  if (!Platform.isAndroid) {
    throw UnsupportedError('Opening legal links is not supported here yet.');
  }
  await _systemChannel.invokeMethod<void>(
    'openExternalUrl',
    <String, Object?>{'url': uri.toString()},
  );
}

Future<bool> hasPrivacyConsent() async {
  final preferences = await SharedPreferences.getInstance();
  return preferences.getString(_privacyConsentPreference) ==
      _privacyConsentVersion;
}

Future<void> savePrivacyConsent() async {
  final preferences = await SharedPreferences.getInstance();
  await preferences.setString(
    _privacyConsentPreference,
    _privacyConsentVersion,
  );
}

Future<bool> showPrivacyConsentCard(BuildContext context) async {
  final result = await showGeneralDialog<bool>(
    context: context,
    barrierDismissible: false,
    barrierLabel: '隐私声明',
    barrierColor: const Color(0x4d39364c),
    transitionDuration: const Duration(milliseconds: 460),
    pageBuilder: (dialogContext, _, __) => const _PrivacyConsentDialog(),
    transitionBuilder: (_, animation, __, child) {
      final fade = CurvedAnimation(
        parent: animation,
        curve: const Interval(0, 0.78, curve: Curves.easeOutCubic),
        reverseCurve: Curves.easeInCubic,
      );
      final spring = CurvedAnimation(
        parent: animation,
        curve: Curves.easeOutBack,
        reverseCurve: Curves.easeInCubic,
      );
      return FadeTransition(
        opacity: fade,
        child: SlideTransition(
          position: Tween<Offset>(
            begin: const Offset(0, 0.055),
            end: Offset.zero,
          ).animate(spring),
          child: ScaleTransition(
            scale: Tween<double>(begin: 0.965, end: 1).animate(spring),
            child: child,
          ),
        ),
      );
    },
  );
  if (result == true) {
    await savePrivacyConsent();
    return true;
  }
  return false;
}

class PrivacyConsentGate extends StatefulWidget {
  const PrivacyConsentGate({required this.child, super.key});

  final Widget child;

  @override
  State<PrivacyConsentGate> createState() => _PrivacyConsentGateState();
}

class _PrivacyConsentGateState extends State<PrivacyConsentGate>
    with SingleTickerProviderStateMixin {
  bool _checking = true;
  bool _accepted = false;
  bool _dialogOpen = false;
  late final AnimationController _entrance;

  @override
  void initState() {
    super.initState();
    _entrance = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 850),
    )..forward();
    _check();
  }

  @override
  void dispose() {
    _entrance.dispose();
    super.dispose();
  }

  Future<void> _check() async {
    final accepted = await hasPrivacyConsent();
    if (!mounted) return;
    setState(() {
      _checking = false;
      _accepted = accepted;
    });
    if (!accepted) {
      releaseXiaoyouFirstFrame();
      WidgetsBinding.instance.addPostFrameCallback((_) => _show());
    }
  }

  Future<void> _show() async {
    if (!mounted || _dialogOpen || _accepted) return;
    _dialogOpen = true;
    final accepted = await showPrivacyConsentCard(context);
    _dialogOpen = false;
    if (mounted && accepted) setState(() => _accepted = true);
  }

  @override
  Widget build(BuildContext context) {
    if (_accepted) return widget.child;
    final animation = CurvedAnimation(
      parent: _entrance,
      curve: Curves.easeOutCubic,
    );
    return Scaffold(
      backgroundColor: const Color(0xfff7f6ff),
      body: Stack(
        fit: StackFit.expand,
        children: [
          Image.asset(
            _loginBackgroundAsset,
            fit: BoxFit.fill,
            filterQuality: FilterQuality.high,
          ),
          const DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [Color(0x08ffffff), Color(0x10ffffff), Color(0x38ffffff)],
              ),
            ),
          ),
          SafeArea(
            child: FadeTransition(
              opacity: animation,
              child: SlideTransition(
                position: Tween<Offset>(
                  begin: const Offset(0, 0.025),
                  end: Offset.zero,
                ).animate(animation),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(30, 72, 30, 24),
                  child: Align(
                    alignment: Alignment.topLeft,
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          '小悠',
                          style: TextStyle(
                            color: Color(0xff8b82f1),
                            fontFamily: 'serif',
                            fontFamilyFallback: ['Noto Serif CJK SC', 'Noto Serif SC', 'serif'],
                            fontSize: 56,
                            height: 1.02,
                            fontWeight: FontWeight.w600,
                            letterSpacing: 3.2,
                          ),
                        ),
                        const SizedBox(height: 14),
                        const Text(
                          '遇见小悠，遇见更从容的自己 ♡',
                          style: TextStyle(
                            color: Color(0xff7068c8),
                            fontFamily: 'serif',
                            fontFamilyFallback: ['Noto Serif CJK SC', 'Noto Serif SC', 'serif'],
                            fontSize: 14.5,
                            height: 1.35,
                            fontWeight: FontWeight.w500,
                            letterSpacing: 0.35,
                          ),
                        ),
                        if (!_checking) ...[
                          const SizedBox(height: 22),
                          _TinyPrivacyButton(onTap: _show),
                        ],
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _TinyPrivacyButton extends StatefulWidget {
  const _TinyPrivacyButton({required this.onTap});

  final VoidCallback onTap;

  @override
  State<_TinyPrivacyButton> createState() => _TinyPrivacyButtonState();
}

class _TinyPrivacyButtonState extends State<_TinyPrivacyButton> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTapDown: (_) => setState(() => _pressed = true),
      onTapCancel: () => setState(() => _pressed = false),
      onTapUp: (_) {
        setState(() => _pressed = false);
        widget.onTap();
      },
      child: AnimatedScale(
        scale: _pressed ? 0.96 : 1,
        duration: const Duration(milliseconds: 140),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 10),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.70),
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: Colors.white.withValues(alpha: 0.92)),
          ),
          child: const Text(
            '查看隐私说明',
            style: TextStyle(
              color: Color(0xff6f68ae),
              fontSize: 12.5,
              fontWeight: FontWeight.w700,
            ),
          ),
        ),
      ),
    );
  }
}

class _PrivacyConsentDialog extends StatelessWidget {
  const _PrivacyConsentDialog();

  Future<void> _openDocument(BuildContext context, String url) async {
    try {
      await openLegalUrl(url);
    } catch (_) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('暂时无法打开页面，请检查网络后重试')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.sizeOf(context);
    return SafeArea(
      child: Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 22),
          child: ConstrainedBox(
            constraints: BoxConstraints(
              maxWidth: 430,
              maxHeight: size.height - 44,
            ),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(34),
              child: BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 28, sigmaY: 28),
                child: Material(
                  color: Colors.transparent,
                  child: Container(
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.955),
                      borderRadius: BorderRadius.circular(34),
                      border: Border.all(
                        color: Colors.white.withValues(alpha: 0.96),
                        width: 1.2,
                      ),
                      boxShadow: const [
                        BoxShadow(
                          color: Color(0x28645c8e),
                          blurRadius: 58,
                          offset: Offset(0, 24),
                        ),
                      ],
                    ),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Padding(
                          padding: const EdgeInsets.fromLTRB(22, 18, 22, 9),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const _PrivacyMark(),
                              const SizedBox(width: 14),
                              const Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      '隐私与安全',
                                      style: TextStyle(
                                        color: _ink,
                                        fontSize: 24,
                                        height: 1.15,
                                        fontWeight: FontWeight.w800,
                                        letterSpacing: -0.3,
                                      ),
                                    ),
                                    SizedBox(height: 6),
                                    Text(
                                      '在登录之前，请先了解小悠如何使用必要信息。',
                                      style: TextStyle(
                                        color: _muted,
                                        fontSize: 13.2,
                                        height: 1.5,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        ),
                        Flexible(
                          child: SingleChildScrollView(
                            physics: const BouncingScrollPhysics(),
                            padding: const EdgeInsets.fromLTRB(22, 4, 22, 5),
                            child: const Column(
                              children: [
                                _PrivacyItem(
                                  icon: Icons.person_outline_rounded,
                                  title: '账号与安全',
                                  detail: '账号、绑定邮箱、登录状态与必要的安全验证信息。密码不会以明文形式保存。',
                                ),
                                _PrivacyItem(
                                  icon: Icons.forum_outlined,
                                  title: '聊天与记忆',
                                  detail: '你主动发送的文字、语音、图片，以及为了保持对话连续性所需的聊天与记忆数据。',
                                ),
                                _PrivacyItem(
                                  icon: Icons.content_paste_outlined,
                                  title: '剪切板',
                                  detail: '只在你主动粘贴或打开相关编辑操作时使用，不会在后台主动读取。',
                                ),
                                _PrivacyItem(
                                  icon: Icons.notifications_none_rounded,
                                  title: '设备与通知',
                                  detail: '必要的设备登录信息与通知标识，用于安全登录、消息同步和系统提醒。',
                                ),
                                _PrivacyItem(
                                  icon: Icons.lock_outline_rounded,
                                  title: '独立空间',
                                  detail: '不同账号的数据、记忆和会话彼此隔离，不会与其他用户混用。',
                                ),
                              ],
                            ),
                          ),
                        ),
                        Padding(
                          padding: const EdgeInsets.fromLTRB(20, 4, 20, 14),
                          child: Column(
                            children: [
                              Row(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  TextButton(
                                    onPressed: () => _openDocument(
                                      context,
                                      xiaoyouPrivacyPolicyUrl,
                                    ),
                                    style: TextButton.styleFrom(
                                      foregroundColor: _violetDeep,
                                    ),
                                    child: const Text('隐私政策'),
                                  ),
                                  const Text(
                                    '·',
                                    style: TextStyle(color: Color(0xffb6b2c5)),
                                  ),
                                  TextButton(
                                    onPressed: () => _openDocument(
                                      context,
                                      xiaoyouUserAgreementUrl,
                                    ),
                                    style: TextButton.styleFrom(
                                      foregroundColor: _violetDeep,
                                    ),
                                    child: const Text('用户协议'),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 2),
                              _ConsentPrimaryButton(
                                onTap: () {
                                  HapticFeedback.mediumImpact();
                                  Navigator.pop(context, true);
                                },
                              ),
                              const SizedBox(height: 5),
                              TextButton(
                                onPressed: () => Navigator.pop(context, false),
                                style: TextButton.styleFrom(
                                  foregroundColor: const Color(0xffaaa6b9),
                                ),
                                child: const Text('暂不同意'),
                              ),
                            ],
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
    );
  }
}

class _PrivacyMark extends StatefulWidget {
  const _PrivacyMark();

  @override
  State<_PrivacyMark> createState() => _PrivacyMarkState();
}

class _PrivacyMarkState extends State<_PrivacyMark>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1650),
      lowerBound: 0,
      upperBound: 1,
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final t = Curves.easeInOut.transform(_controller.value);
        return Transform.scale(
          scale: 0.985 + (0.025 * t),
          child: Container(
            width: 46,
            height: 46,
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                colors: [Color(0xffa79ff8), Color(0xff8275ef)],
              ),
              borderRadius: BorderRadius.circular(16),
              boxShadow: [
                BoxShadow(
                  color: _violet.withValues(alpha: 0.16 + (0.08 * t)),
                  blurRadius: 18 + (8 * t),
                  offset: const Offset(0, 7),
                ),
              ],
            ),
            child: const Icon(
              Icons.lock_outline_rounded,
              color: Colors.white,
              size: 22,
            ),
          ),
        );
      },
    );
  }
}

class _PrivacyItem extends StatelessWidget {
  const _PrivacyItem({
    required this.icon,
    required this.title,
    required this.detail,
  });

  final IconData icon;
  final String title;
  final String detail;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 7),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0xfff8f7fc),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: const Color(0xffece9f5)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 34,
            height: 34,
            decoration: BoxDecoration(
              color: const Color(0xffefedff),
              borderRadius: BorderRadius.circular(11),
            ),
            child: Icon(icon, color: _violetDeep, size: 18),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    color: _ink,
                    fontSize: 13.2,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  detail,
                  style: const TextStyle(
                    color: _muted,
                    fontSize: 11.9,
                    height: 1.38,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ConsentPrimaryButton extends StatefulWidget {
  const _ConsentPrimaryButton({required this.onTap});

  final VoidCallback onTap;

  @override
  State<_ConsentPrimaryButton> createState() => _ConsentPrimaryButtonState();
}

class _ConsentPrimaryButtonState extends State<_ConsentPrimaryButton> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTapDown: (_) => setState(() => _pressed = true),
      onTapCancel: () => setState(() => _pressed = false),
      onTapUp: (_) {
        setState(() => _pressed = false);
        widget.onTap();
      },
      child: AnimatedScale(
        scale: _pressed ? 0.975 : 1,
        duration: const Duration(milliseconds: 150),
        child: Container(
          width: double.infinity,
          height: 54,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            gradient: const LinearGradient(
              colors: [Color(0xff9b91fa), Color(0xff7e70ef)],
            ),
            borderRadius: BorderRadius.circular(27),
            boxShadow: const [
              BoxShadow(
                color: Color(0x2b8174ef),
                blurRadius: 22,
                offset: Offset(0, 9),
              ),
            ],
          ),
          child: const Text(
            '同意并继续',
            style: TextStyle(
              color: Colors.white,
              fontSize: 16,
              fontWeight: FontWeight.w800,
            ),
          ),
        ),
      ),
    );
  }
}

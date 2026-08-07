import 'dart:io';

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
    barrierColor: const Color(0xb54d43c7),
    transitionDuration: const Duration(milliseconds: 260),
    pageBuilder: (dialogContext, _, __) => const _PrivacyConsentDialog(),
    transitionBuilder: (_, animation, __, child) {
      final curved = CurvedAnimation(
        parent: animation,
        curve: Curves.easeOutCubic,
        reverseCurve: Curves.easeInCubic,
      );
      return FadeTransition(
        opacity: curved,
        child: ScaleTransition(
          scale: Tween<double>(begin: 0.94, end: 1).animate(curved),
          child: child,
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

/// Prevents account/network initialization until first-run consent is given.
class PrivacyConsentGate extends StatefulWidget {
  const PrivacyConsentGate({required this.child, super.key});

  final Widget child;

  @override
  State<PrivacyConsentGate> createState() => _PrivacyConsentGateState();
}

class _PrivacyConsentGateState extends State<PrivacyConsentGate> {
  bool _checking = true;
  bool _accepted = false;
  bool _dialogOpen = false;

  @override
  void initState() {
    super.initState();
    _check();
  }

  Future<void> _check() async {
    final accepted = await hasPrivacyConsent();
    if (!mounted) return;
    setState(() {
      _checking = false;
      _accepted = accepted;
    });
    if (!accepted) WidgetsBinding.instance.addPostFrameCallback((_) => _show());
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
    return Scaffold(
      body: DecoratedBox(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xff5045ef), Color(0xff7964f4), Color(0xffa58af4)],
          ),
        ),
        child: Center(
          child: AnimatedOpacity(
            duration: const Duration(milliseconds: 350),
            opacity: _checking ? 0.45 : 1,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(
                  Icons.auto_awesome_rounded,
                  color: Color(0xffe5dcff),
                  size: 24,
                ),
                const SizedBox(height: 12),
                const Text(
                  '小 悠',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 48,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 7,
                    shadows: [
                      Shadow(color: Color(0x66524af0), blurRadius: 28),
                    ],
                  ),
                ),
                const SizedBox(height: 10),
                Text(
                  '你的专属 AI 陪伴',
                  style: TextStyle(
                    color: Colors.white.withValues(alpha: 0.82),
                    fontSize: 15,
                    letterSpacing: 1.6,
                  ),
                ),
                if (!_checking) ...[
                  const SizedBox(height: 30),
                  SizedBox(
                    height: 52,
                    child: FilledButton.icon(
                      onPressed: _show,
                      style: FilledButton.styleFrom(
                        foregroundColor: const Color(0xff5140e8),
                        backgroundColor: Colors.white,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(26),
                        ),
                      ),
                      icon: const Icon(Icons.verified_user_outlined),
                      label: const Text('查看隐私声明'),
                    ),
                  ),
                ],
              ],
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
    final height = MediaQuery.sizeOf(context).height;
    return SafeArea(
      child: Center(
        child: Material(
          color: Colors.transparent,
          child: Container(
            width: 420,
            constraints: BoxConstraints(maxHeight: height - 48),
            margin: const EdgeInsets.symmetric(horizontal: 22),
            decoration: BoxDecoration(
              color: const Color(0xff6658ec),
              borderRadius: BorderRadius.circular(30),
              border: Border.all(color: Colors.white.withValues(alpha: 0.9)),
              boxShadow: const [
                BoxShadow(
                  color: Color(0x55332988),
                  blurRadius: 48,
                  offset: Offset(0, 22),
                ),
              ],
            ),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(30),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.fromLTRB(24, 22, 24, 20),
                    decoration: const BoxDecoration(
                      gradient: LinearGradient(
                        colors: [Color(0xff7667f5), Color(0xff8d76f2)],
                      ),
                    ),
                    child: const Row(
                      children: [
                        _ShieldMark(),
                        SizedBox(width: 14),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                '隐私声明',
                                style: TextStyle(
                                  color: Colors.white,
                                  fontSize: 22,
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                              SizedBox(height: 4),
                              Text(
                                '请在登录小悠前阅读并作出选择',
                                style: TextStyle(
                                  color: Color(0xffded9ff),
                                  fontSize: 13,
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
                      padding: const EdgeInsets.fromLTRB(24, 20, 24, 8),
                      child: const Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '为了提供账号登录、聊天、语音、图片、通知和多设备同步，小悠会在必要范围内处理以下信息：',
                            style: TextStyle(
                              color: Color(0xfff4f1ff),
                              fontSize: 14,
                              height: 1.65,
                            ),
                          ),
                          SizedBox(height: 15),
                          _PrivacyItem(
                            icon: Icons.person_outline_rounded,
                            title: '账号与安全',
                            detail:
                                '账号、绑定邮箱、密码不可逆哈希、邮箱验证码及登录状态；邮箱仅用于注册验证与找回密码，服务器不保存明文密码。',
                          ),
                          _PrivacyItem(
                            icon: Icons.forum_outlined,
                            title: '聊天与媒体',
                            detail: '你主动发送的文字、语音和图片，以及为保持对话连续性所需的聊天记录与记忆。',
                          ),
                          _PrivacyItem(
                            icon: Icons.notifications_none_rounded,
                            title: '设备与通知',
                            detail: '设备登录信息和通知标识，用于安全登录、消息同步及系统通知。',
                          ),
                          _PrivacyItem(
                            icon: Icons.lock_outline_rounded,
                            title: '账号相互隔离',
                            detail:
                                '普通用户按内部用户编号使用独立数据目录和记忆数据库；不会与运营者本人或其他用户的记忆混用。',
                          ),
                          SizedBox(height: 6),
                          Text(
                            '点击“同意并继续”即表示你已阅读并同意《隐私政策》和《用户协议》。你可以在系统设置中随时再次查看。',
                            style: TextStyle(
                              color: Color(0xffded9ff),
                              fontSize: 12.5,
                              height: 1.55,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(20, 10, 20, 20),
                    child: Column(
                      children: [
                        Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            TextButton(
                              style: TextButton.styleFrom(
                                foregroundColor: Colors.white,
                              ),
                              onPressed: () => _openDocument(
                                context,
                                xiaoyouPrivacyPolicyUrl,
                              ),
                              child: const Text('《隐私政策》'),
                            ),
                            const Text('·',
                                style: TextStyle(color: Color(0xffded9ff))),
                            TextButton(
                              style: TextButton.styleFrom(
                                foregroundColor: Colors.white,
                              ),
                              onPressed: () => _openDocument(
                                context,
                                xiaoyouUserAgreementUrl,
                              ),
                              child: const Text('《用户协议》'),
                            ),
                          ],
                        ),
                        const SizedBox(height: 4),
                        SizedBox(
                          width: double.infinity,
                          height: 52,
                          child: FilledButton(
                            onPressed: () => Navigator.pop(context, true),
                            style: FilledButton.styleFrom(
                              backgroundColor: Colors.white,
                              foregroundColor: const Color(0xff5140e8),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(18),
                              ),
                            ),
                            child: const Text('同意并继续'),
                          ),
                        ),
                        const SizedBox(height: 6),
                        TextButton(
                          onPressed: () => Navigator.pop(context, false),
                          child: const Text(
                            '暂不同意',
                            style: TextStyle(color: Color(0xffded9ff)),
                          ),
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
    );
  }
}

class _ShieldMark extends StatelessWidget {
  const _ShieldMark();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 48,
      height: 48,
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(16),
      ),
      child: const Icon(
        Icons.verified_user_outlined,
        color: Colors.white,
        size: 25,
      ),
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
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Icon(icon, color: Colors.white, size: 19),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 14,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  detail,
                  style: const TextStyle(
                    color: Color(0xffded9ff),
                    fontSize: 12.5,
                    height: 1.5,
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

import 'dart:async';

import 'package:flutter/material.dart';

import 'legal.dart';
import 'session_store.dart';
import 'xiaoyou_api.dart';

const _ink = Color(0xff34272e);
const _muted = Color(0xff806f78);
const _rose = Color(0xffad4f7d);
const _defaultBaseUrl = 'https://xiaoyou.yoyoyan.cn/xiaoyou-app';

class AccountAccessResult {
  const AccountAccessResult({
    required this.baseUrl,
    required this.deviceId,
    required this.remember,
    required this.login,
  });

  final String baseUrl;
  final String deviceId;
  final bool remember;
  final XiaoyouLoginResult login;
}

class AccountAccessSheet extends StatefulWidget {
  const AccountAccessSheet({required this.saved, super.key});

  final SavedConnection? saved;

  @override
  State<AccountAccessSheet> createState() => _AccountAccessSheetState();
}

class _AccountAccessSheetState extends State<AccountAccessSheet> {
  late final TextEditingController _base;
  late final TextEditingController _email;
  late final TextEditingController _code;
  late final TextEditingController _device;
  XiaoyouAuthConfig? _config;
  bool _remember = true;
  bool _agreed = false;
  bool _busy = false;
  bool _codeSent = false;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _base = TextEditingController(
      text: widget.saved?.baseUrl ?? _defaultBaseUrl,
    );
    _email = TextEditingController(text: widget.saved?.accountId ?? '');
    _code = TextEditingController();
    _device = TextEditingController(
      text: widget.saved?.deviceId ?? 'xiaoyou-phone',
    );
    unawaited(_loadInitialState());
  }

  @override
  void dispose() {
    _base.dispose();
    _email.dispose();
    _code.dispose();
    _device.dispose();
    super.dispose();
  }

  Future<void> _loadInitialState() async {
    final consent = await hasPrivacyConsent();
    if (!mounted) return;
    setState(() => _agreed = consent);
    if (consent) {
      unawaited(_loadConfig());
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_requestConsent());
    });
  }

  Future<bool> _requestConsent() async {
    if (_agreed) return true;
    final accepted = await showPrivacyConsentCard(context);
    if (!mounted) return false;
    setState(() => _agreed = accepted);
    if (accepted && _config == null) {
      unawaited(_loadConfig());
    }
    return accepted;
  }

  Future<void> _loadConfig() async {
    try {
      final value = await XiaoyouApi.authConfig(_base.text.trim());
      if (mounted) setState(() => _config = value);
    } catch (_) {
      // The submit action still reports the concrete network/service error.
    }
  }

  Future<bool> _ensureConsent() => _requestConsent();

  bool _validateCommon() {
    if (!_base.text.trim().startsWith('https://') ||
        _device.text.trim().isEmpty) {
      setState(() => _error = '登录服务暂时不可用，请稍后重试');
      return false;
    }
    final email = _email.text.trim();
    if (email.isEmpty || !email.contains('@')) {
      setState(() => _error = '请输入有效的邮箱地址');
      return false;
    }
    if (_codeSent && _code.text.trim().length != 6) {
      setState(() => _error = '请输入 6 位邮箱验证码');
      return false;
    }
    return true;
  }

  Future<void> _submit() async {
    if (_busy || !await _ensureConsent() || !_validateCommon()) return;
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      if (!_codeSent) {
        await _sendCode();
        return;
      }
      final login = await XiaoyouApi.verifyEmailCode(
        baseUrl: _base.text.trim(),
        email: _email.text.trim(),
        code: _code.text.trim(),
        deviceId: _device.text.trim(),
        remember: _remember,
      );
      _finish(login);
    } catch (error) {
      if (mounted) setState(() => _error = _friendly(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _sendCode({bool resend = false}) async {
    final response = await XiaoyouApi.requestEmailCode(
      baseUrl: _base.text.trim(),
      email: _email.text.trim(),
    );
    if (!mounted) return;
    setState(() {
      _codeSent = true;
      _code.clear();
      final debugCode = '${response['debug_code'] ?? ''}';
      if (debugCode.isNotEmpty) _code.text = debugCode;
    });
    if (resend) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('新的验证码已发送')),
      );
    }
  }

  Future<void> _resendCode() async {
    if (_busy || !_validateCommon()) return;
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      await _sendCode(resend: true);
    } catch (error) {
      if (mounted) setState(() => _error = _friendly(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _changeEmail() {
    setState(() {
      _codeSent = false;
      _code.clear();
      _error = '';
    });
  }

  void _finish(XiaoyouLoginResult login) {
    if (!mounted) return;
    Navigator.pop(
      context,
      AccountAccessResult(
        baseUrl: _base.text.trim(),
        deviceId: login.deviceId,
        remember: _remember,
        login: login,
      ),
    );
  }

  String _friendly(Object error) {
    final value = '$error';
    if (value.contains('invalid_email')) return '请输入有效的邮箱地址';
    if (value.contains('invalid_or_expired_code')) return '验证码错误或已过期，请重新获取';
    if (value.contains('email_code_too_frequent')) return '验证码刚刚已经发送，请稍后再获取';
    if (value.contains('account_disabled')) return '这个账号当前不可用';
    if (value.contains('email_service_unavailable')) return '验证邮件服务暂不可用';
    return '暂时无法完成登录，请检查网络后重试';
  }

  InputDecoration _decoration({
    required String label,
    Widget? prefix,
    Widget? suffix,
  }) {
    return InputDecoration(
      labelText: label,
      labelStyle: const TextStyle(color: _muted),
      prefixIcon: prefix,
      suffixIcon: suffix,
      filled: true,
      fillColor: Colors.white.withValues(alpha: 0.84),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 17),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(18),
        borderSide: const BorderSide(color: Color(0xffecdee5)),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(18),
        borderSide: const BorderSide(color: _rose, width: 1.4),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(18),
        borderSide: const BorderSide(color: Color(0xffc84670)),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.viewInsetsOf(context).bottom;
    final emailReady = _config?.emailLogin != false;
    return Padding(
      padding: EdgeInsets.only(bottom: bottomInset),
      child: Material(
        color: Colors.transparent,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(34)),
        clipBehavior: Clip.antiAlias,
        child: Container(
          constraints: BoxConstraints(
            maxHeight: MediaQuery.sizeOf(context).height * 0.95,
          ),
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [Color(0xfffffbfd), Color(0xfffff4f9), Color(0xfff7f1ff)],
            ),
          ),
          child: Stack(
            children: [
              const Positioned(
                right: -65,
                top: -70,
                child: _SoftGlow(size: 220, color: Color(0x55f2a2c7)),
              ),
              const Positioned(
                left: -80,
                bottom: 30,
                child: _SoftGlow(size: 210, color: Color(0x3b9e86e8)),
              ),
              SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(24, 12, 24, 28),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Container(
                      width: 42,
                      height: 4,
                      decoration: BoxDecoration(
                        color: const Color(0xffd9cbd2),
                        borderRadius: BorderRadius.circular(99),
                      ),
                    ),
                    const SizedBox(height: 20),
                    _LoginHeader(
                      title: '登录小悠',
                      subtitle: _codeSent
                          ? '验证码已经发到 ${_email.text.trim()}，10 分钟内有效'
                          : '输入邮箱，获取验证码后即可登录；首次验证会自动创建账号',
                    ),
                    const SizedBox(height: 24),
                    TextField(
                      controller: _email,
                      enabled: !_codeSent && !_busy,
                      keyboardType: TextInputType.emailAddress,
                      textInputAction: _codeSent
                          ? TextInputAction.next
                          : TextInputAction.done,
                      autofillHints: const [AutofillHints.email],
                      decoration: _decoration(
                        label: '邮箱',
                        prefix: const _AssetFieldIcon('assets/email-login.png'),
                        suffix: _codeSent
                            ? TextButton(
                                onPressed: _busy ? null : _changeEmail,
                                child: const Text('更换'),
                              )
                            : null,
                      ),
                    ),
                    if (_codeSent) ...[
                      const SizedBox(height: 13),
                      TextField(
                        controller: _code,
                        keyboardType: TextInputType.number,
                        textInputAction: TextInputAction.done,
                        autofillHints: const [AutofillHints.oneTimeCode],
                        maxLength: 6,
                        onSubmitted: (_) => unawaited(_submit()),
                        decoration: _decoration(
                          label: '6 位邮箱验证码',
                          prefix: const Icon(
                            Icons.mark_email_read_outlined,
                            color: _rose,
                            size: 21,
                          ),
                        ).copyWith(counterText: ''),
                      ),
                      Align(
                        alignment: Alignment.centerRight,
                        child: TextButton(
                          onPressed: _busy ? null : _resendCode,
                          child: const Text('重新获取验证码'),
                        ),
                      ),
                    ] else
                      const SizedBox(height: 8),
                    Row(
                      children: [
                        _RememberToggle(
                          value: _remember,
                          onChanged: (value) =>
                              setState(() => _remember = value),
                        ),
                        const Spacer(),
                        const Text(
                          '无需密码',
                          style: TextStyle(
                            color: Color(0xff9d8792),
                            fontSize: 12.5,
                          ),
                        ),
                      ],
                    ),
                    _ConsentLine(
                      agreed: _agreed,
                      onTap: () => unawaited(_requestConsent()),
                    ),
                    if (!emailReady) ...[
                      const SizedBox(height: 10),
                      _InlineNotice(text: '邮箱验证码服务正在维护，请稍后再试'),
                    ],
                    if (_error.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      _InlineNotice(text: _error),
                    ],
                    const SizedBox(height: 15),
                    AnimatedOpacity(
                      duration: const Duration(milliseconds: 180),
                      opacity: _agreed && emailReady ? 1 : 0.5,
                      child: SizedBox(
                        width: double.infinity,
                        height: 54,
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            gradient: const LinearGradient(
                              colors: [Color(0xffb64f80), Color(0xff8b65bc)],
                            ),
                            borderRadius: BorderRadius.circular(19),
                            boxShadow: const [
                              BoxShadow(
                                color: Color(0x2aa64678),
                                blurRadius: 20,
                                offset: Offset(0, 9),
                              ),
                            ],
                          ),
                          child: FilledButton(
                            onPressed: _busy || !_agreed || !emailReady
                                ? null
                                : _submit,
                            style: FilledButton.styleFrom(
                              backgroundColor: Colors.transparent,
                              disabledBackgroundColor: Colors.transparent,
                              shadowColor: Colors.transparent,
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(19),
                              ),
                            ),
                            child: _busy
                                ? const SizedBox(
                                    width: 22,
                                    height: 22,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                      color: Colors.white,
                                    ),
                                  )
                                : Text(
                                    _codeSent ? '验证并登录' : '获取邮箱验证码',
                                    style: const TextStyle(
                                      fontSize: 16,
                                      fontWeight: FontWeight.w700,
                                    ),
                                  ),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 14),
                    const Text(
                      '验证码仅用于确认邮箱归属，不会向其他用户公开你的邮箱',
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        color: Color(0xff9b8992),
                        fontSize: 11.5,
                        height: 1.45,
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
}

class _LoginHeader extends StatelessWidget {
  const _LoginHeader({required this.title, required this.subtitle});

  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Stack(
          clipBehavior: Clip.none,
          children: [
            Container(
              width: 82,
              height: 82,
              padding: const EdgeInsets.all(3),
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: Colors.white,
                border: Border.all(color: const Color(0xffffd8e8)),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x2aa64c78),
                    blurRadius: 26,
                    offset: Offset(0, 12),
                  ),
                ],
              ),
              child: const ClipOval(
                child: Image(
                  image: AssetImage('assets/xiaoyou-avatar.png'),
                  fit: BoxFit.cover,
                ),
              ),
            ),
            Positioned(
              right: -2,
              bottom: 5,
              child: Container(
                width: 18,
                height: 18,
                decoration: BoxDecoration(
                  color: const Color(0xff36b88b),
                  shape: BoxShape.circle,
                  border: Border.all(color: Colors.white, width: 3),
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 14),
        Text(
          title,
          style: const TextStyle(
            color: _ink,
            fontSize: 25,
            fontWeight: FontWeight.w800,
          ),
        ),
        const SizedBox(height: 6),
        Text(
          subtitle,
          textAlign: TextAlign.center,
          style: const TextStyle(color: _muted, fontSize: 13, height: 1.45),
        ),
      ],
    );
  }
}

class _AssetFieldIcon extends StatelessWidget {
  const _AssetFieldIcon(this.asset);

  final String asset;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(13),
      child: Image.asset(asset, width: 22, height: 22),
    );
  }
}

class _RememberToggle extends StatelessWidget {
  const _RememberToggle({required this.value, required this.onChanged});

  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => onChanged(!value),
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 180),
              width: 20,
              height: 20,
              decoration: BoxDecoration(
                color: value ? _rose : Colors.transparent,
                borderRadius: BorderRadius.circular(7),
                border: Border.all(
                  color: value ? _rose : const Color(0xffbaaab2),
                ),
              ),
              child: value
                  ? const Icon(Icons.check_rounded,
                      size: 15, color: Colors.white)
                  : null,
            ),
            const SizedBox(width: 8),
            const Text('记住登录', style: TextStyle(color: _muted, fontSize: 13)),
          ],
        ),
      ),
    );
  }
}

class _ConsentLine extends StatelessWidget {
  const _ConsentLine({required this.agreed, required this.onTap});

  final bool agreed;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(14),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 1),
              child: Icon(
                agreed ? Icons.check_circle_rounded : Icons.circle_outlined,
                color: agreed ? _rose : const Color(0xffb5a5ad),
                size: 19,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text.rich(
                TextSpan(
                  style: const TextStyle(
                    color: _muted,
                    fontSize: 12.5,
                    height: 1.45,
                  ),
                  children: [
                    TextSpan(text: agreed ? '已阅读并同意 ' : '点击阅读并同意 '),
                    const TextSpan(
                      text: '《隐私政策》与《用户协议》',
                      style:
                          TextStyle(color: _rose, fontWeight: FontWeight.w600),
                    ),
                  ],
                ),
              ),
            ),
            const Icon(Icons.chevron_right_rounded, color: Color(0xffaa98a1)),
          ],
        ),
      ),
    );
  }
}

class _InlineNotice extends StatelessWidget {
  const _InlineNotice({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
      decoration: BoxDecoration(
        color: const Color(0xffffeaf1),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0xffffccdc)),
      ),
      child: Text(text, style: const TextStyle(color: Color(0xff923558))),
    );
  }
}

class _SoftGlow extends StatelessWidget {
  const _SoftGlow({required this.size, required this.color});

  final double size;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: RadialGradient(colors: [color, color.withValues(alpha: 0)]),
      ),
    );
  }
}

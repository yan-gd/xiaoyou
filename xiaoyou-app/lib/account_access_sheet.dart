import 'dart:async';

import 'package:flutter/material.dart';

import 'legal.dart';
import 'session_store.dart';
import 'xiaoyou_api.dart';

const _ink = Color(0xff34272e);
const _muted = Color(0xff806f78);
const _rose = Color(0xffad4f7d);
const _defaultBaseUrl = 'https://xiaoyou.yoyoyan.cn/xiaoyou-app';

enum _AccountMode { login, register, reset }

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
  late final TextEditingController _password;
  late final TextEditingController _confirmPassword;
  late final TextEditingController _code;
  late final TextEditingController _device;
  _AccountMode _mode = _AccountMode.login;
  XiaoyouAuthConfig? _config;
  bool _remember = true;
  bool _agreed = false;
  bool _busy = false;
  bool _codeSent = false;
  bool _hidePassword = true;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _base = TextEditingController(
      text: widget.saved?.baseUrl ?? _defaultBaseUrl,
    );
    _email = TextEditingController(text: widget.saved?.accountId ?? '');
    _password = TextEditingController();
    _confirmPassword = TextEditingController();
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
    _password.dispose();
    _confirmPassword.dispose();
    _code.dispose();
    _device.dispose();
    super.dispose();
  }

  Future<void> _loadInitialState() async {
    final consent = await hasPrivacyConsent();
    if (!mounted) return;
    setState(() => _agreed = consent);
    if (consent) {
      await _loadConfig();
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
      // Existing accounts can still use email login during config outages.
    }
  }

  void _switchMode(_AccountMode value) {
    setState(() {
      _mode = value;
      _error = '';
      _codeSent = false;
      _code.clear();
      _confirmPassword.clear();
    });
  }

  Future<bool> _ensureConsent() => _requestConsent();

  bool _validateCommon() {
    if (!_base.text.trim().startsWith('https://') ||
        _device.text.trim().isEmpty) {
      setState(() => _error = '登录服务暂时不可用，请稍后重试');
      return false;
    }
    if (_email.text.trim().isEmpty) {
      setState(() => _error = '请输入邮箱或账号');
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
      switch (_mode) {
        case _AccountMode.login:
          final login = await XiaoyouApi.login(
            baseUrl: _base.text.trim(),
            username: _email.text.trim(),
            password: _password.text,
            deviceId: _device.text.trim(),
            remember: _remember,
          );
          _finish(login);
          return;
        case _AccountMode.register:
          await _register();
          return;
        case _AccountMode.reset:
          await _resetPassword();
          return;
      }
    } catch (error) {
      if (mounted) setState(() => _error = _friendly(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _register() async {
    if (!_codeSent) {
      if (_password.text != _confirmPassword.text) {
        throw const FormatException('password_mismatch');
      }
      final response = await XiaoyouApi.requestRegistration(
        baseUrl: _base.text.trim(),
        email: _email.text.trim(),
        password: _password.text,
      );
      if (!mounted) return;
      setState(() {
        _codeSent = true;
        final debugCode = '${response['debug_code'] ?? ''}';
        if (debugCode.isNotEmpty) _code.text = debugCode;
      });
      return;
    }
    final login = await XiaoyouApi.verifyRegistration(
      baseUrl: _base.text.trim(),
      email: _email.text.trim(),
      code: _code.text.trim(),
      deviceId: _device.text.trim(),
      remember: _remember,
    );
    _finish(login);
  }

  Future<void> _resetPassword() async {
    if (!_codeSent) {
      await XiaoyouApi.requestPasswordReset(
        baseUrl: _base.text.trim(),
        email: _email.text.trim(),
      );
      if (mounted) setState(() => _codeSent = true);
      return;
    }
    if (_password.text != _confirmPassword.text) {
      throw const FormatException('password_mismatch');
    }
    await XiaoyouApi.confirmPasswordReset(
      baseUrl: _base.text.trim(),
      email: _email.text.trim(),
      code: _code.text.trim(),
      password: _password.text,
    );
    if (!mounted) return;
    _switchMode(_AccountMode.login);
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('密码已更新，请使用新密码登录')),
    );
  }

  Future<void> _qqLogin() async {
    if (_busy || !await _ensureConsent()) return;
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      final flow = await XiaoyouApi.startQqLogin(_base.text.trim());
      await openLegalUrl(flow.authorizationUrl);
      for (var attempt = 0; attempt < 150 && mounted; attempt++) {
        await Future<void>.delayed(const Duration(seconds: 2));
        final login = await XiaoyouApi.exchangeQqLogin(
          baseUrl: _base.text.trim(),
          loginId: flow.loginId,
          deviceId: _device.text.trim(),
          remember: _remember,
        );
        if (login != null) {
          _finish(login);
          return;
        }
      }
      throw TimeoutException('qq_login_timeout');
    } catch (error) {
      if (mounted) setState(() => _error = _friendly(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
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
    if (value.contains('invalid_credentials')) return '邮箱（账号）或密码不正确';
    if (value.contains('invalid_email')) return '请输入有效的邮箱地址';
    if (value.contains('email_already_registered')) return '这个邮箱已经注册，可直接登录';
    if (value.contains('invalid_password_length')) {
      return '密码至少 8 位，且不要超过 72 字节';
    }
    if (value.contains('password_mismatch')) {
      return '两次输入的密码不一致';
    }
    if (value.contains('invalid_or_expired_code')) return '验证码错误或已过期';
    if (value.contains('qq_login_unavailable')) return 'QQ 登录尚未配置完成';
    if (value.contains('qq_login_timeout')) return 'QQ 授权等待超时，请重试';
    if (value.contains('email_service_unavailable')) return '验证邮件服务暂不可用';
    return '暂时无法完成操作，请检查网络后重试';
  }

  String get _title => switch (_mode) {
        _AccountMode.login => '登录小悠',
        _AccountMode.register => '创建账号',
        _AccountMode.reset => '找回密码',
      };

  String get _subtitle => switch (_mode) {
        _AccountMode.login => '回来以后，故事继续从上次停下的地方开始',
        _AccountMode.register =>
          _codeSent ? '验证码已经发到邮箱，10 分钟内有效' : '用邮箱创建只属于你的独立账号',
        _AccountMode.reset => _codeSent ? '输入验证码并设置一个新密码' : '我们会向你的注册邮箱发送验证码',
      };

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
                    _LoginHeader(title: _title, subtitle: _subtitle),
                    const SizedBox(height: 22),
                    if (_mode != _AccountMode.reset)
                      _ModeSwitcher(
                        mode: _mode,
                        onChanged: _switchMode,
                      ),
                    const SizedBox(height: 18),
                    TextField(
                      controller: _email,
                      keyboardType: TextInputType.emailAddress,
                      textInputAction: TextInputAction.next,
                      autofillHints: const [
                        AutofillHints.username,
                        AutofillHints.email,
                      ],
                      decoration: _decoration(
                        label: _mode == _AccountMode.login ? '邮箱或账号' : '邮箱',
                        prefix: const _AssetFieldIcon('assets/email-login.png'),
                      ),
                    ),
                    if (!_codeSent || _mode == _AccountMode.login) ...[
                      const SizedBox(height: 13),
                      TextField(
                        controller: _password,
                        obscureText: _hidePassword,
                        autofillHints: [
                          _mode == _AccountMode.login
                              ? AutofillHints.password
                              : AutofillHints.newPassword,
                        ],
                        decoration: _decoration(
                          label: _mode == _AccountMode.login
                              ? '密码'
                              : '设置密码（至少 8 位）',
                          prefix: const Icon(
                            Icons.lock_outline_rounded,
                            color: _rose,
                            size: 21,
                          ),
                          suffix: IconButton(
                            onPressed: () => setState(
                              () => _hidePassword = !_hidePassword,
                            ),
                            icon: Icon(
                              _hidePassword
                                  ? Icons.visibility_off_outlined
                                  : Icons.visibility_outlined,
                              color: _muted,
                            ),
                          ),
                        ),
                      ),
                    ],
                    if (_mode != _AccountMode.login &&
                        (!_codeSent || _mode == _AccountMode.reset)) ...[
                      const SizedBox(height: 13),
                      TextField(
                        controller: _confirmPassword,
                        obscureText: true,
                        decoration: _decoration(
                          label: '再次输入密码',
                          prefix: const Icon(
                            Icons.verified_user_outlined,
                            color: _rose,
                            size: 21,
                          ),
                        ),
                      ),
                    ],
                    if (_codeSent) ...[
                      const SizedBox(height: 13),
                      TextField(
                        controller: _code,
                        keyboardType: TextInputType.number,
                        maxLength: 6,
                        decoration: _decoration(
                          label: '6 位邮箱验证码',
                          prefix: const _AssetFieldIcon(
                            'assets/email-login.png',
                          ),
                        ).copyWith(counterText: ''),
                      ),
                    ],
                    const SizedBox(height: 8),
                    if (_mode == _AccountMode.login)
                      Row(
                        children: [
                          _RememberToggle(
                            value: _remember,
                            onChanged: (value) =>
                                setState(() => _remember = value),
                          ),
                          const Spacer(),
                          TextButton(
                            onPressed: () => _switchMode(_AccountMode.reset),
                            child: const Text('忘记密码？'),
                          ),
                        ],
                      ),
                    _ConsentLine(
                      agreed: _agreed,
                      onTap: () => unawaited(_requestConsent()),
                    ),
                    if (_error.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.symmetric(
                          horizontal: 14,
                          vertical: 11,
                        ),
                        decoration: BoxDecoration(
                          color: const Color(0xffffeaf1),
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(color: const Color(0xffffccdc)),
                        ),
                        child: Text(
                          _error,
                          style: const TextStyle(color: Color(0xff923558)),
                        ),
                      ),
                    ],
                    const SizedBox(height: 15),
                    AnimatedOpacity(
                      duration: const Duration(milliseconds: 180),
                      opacity: _agreed ? 1 : 0.5,
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
                            onPressed: _busy || !_agreed ? null : _submit,
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
                                    _mode == _AccountMode.login
                                        ? '登录'
                                        : (_codeSent ? '确认' : '发送验证码'),
                                    style: const TextStyle(
                                      fontSize: 16,
                                      fontWeight: FontWeight.w700,
                                    ),
                                  ),
                          ),
                        ),
                      ),
                    ),
                    if (_mode == _AccountMode.login) ...[
                      const SizedBox(height: 18),
                      const _OrDivider(),
                      const SizedBox(height: 14),
                      SizedBox(
                        width: double.infinity,
                        height: 52,
                        child: OutlinedButton(
                          onPressed:
                              _busy || !_agreed || _config?.qqLogin != true
                                  ? null
                                  : _qqLogin,
                          style: OutlinedButton.styleFrom(
                            backgroundColor:
                                Colors.white.withValues(alpha: 0.7),
                            side: const BorderSide(color: Color(0xffe4d9df)),
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(18),
                            ),
                          ),
                          child: Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Image.asset(
                                'assets/qq-login.png',
                                width: 28,
                                height: 28,
                              ),
                              const SizedBox(width: 10),
                              Text(
                                _config == null
                                    ? '正在准备 QQ 登录…'
                                    : (_config?.qqLogin == true
                                        ? 'QQ 一键登录'
                                        : 'QQ 登录暂不可用'),
                                style: const TextStyle(
                                  color: _ink,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                    const SizedBox(height: 8),
                    if (_mode == _AccountMode.login)
                      TextButton(
                        onPressed: () => _switchMode(_AccountMode.register),
                        child: const Text('还没有账号？立即注册'),
                      )
                    else
                      TextButton(
                        onPressed: () => _switchMode(_AccountMode.login),
                        child: const Text('返回账号登录'),
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

class _ModeSwitcher extends StatelessWidget {
  const _ModeSwitcher({required this.mode, required this.onChanged});

  final _AccountMode mode;
  final ValueChanged<_AccountMode> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 46,
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: const Color(0xfff2e8ed),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        children: [
          _ModeTab(
            label: '登录',
            selected: mode == _AccountMode.login,
            onTap: () => onChanged(_AccountMode.login),
          ),
          _ModeTab(
            label: '注册',
            selected: mode == _AccountMode.register,
            onTap: () => onChanged(_AccountMode.register),
          ),
        ],
      ),
    );
  }
}

class _ModeTab extends StatelessWidget {
  const _ModeTab({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(13),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? Colors.white : Colors.transparent,
            borderRadius: BorderRadius.circular(13),
            boxShadow: selected
                ? const [
                    BoxShadow(
                      color: Color(0x1698426d),
                      blurRadius: 12,
                      offset: Offset(0, 4),
                    ),
                  ]
                : null,
          ),
          child: Text(
            label,
            style: TextStyle(
              color: selected ? _ink : _muted,
              fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
            ),
          ),
        ),
      ),
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

class _OrDivider extends StatelessWidget {
  const _OrDivider();

  @override
  Widget build(BuildContext context) {
    return const Row(
      children: [
        Expanded(child: Divider(color: Color(0xffe6d9df))),
        Padding(
          padding: EdgeInsets.symmetric(horizontal: 12),
          child: Text('其他方式', style: TextStyle(color: _muted, fontSize: 12)),
        ),
        Expanded(child: Divider(color: Color(0xffe6d9df))),
      ],
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

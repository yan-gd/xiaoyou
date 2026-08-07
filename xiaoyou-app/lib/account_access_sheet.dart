import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'legal.dart';
import 'session_store.dart';
import 'xiaoyou_api.dart';

const _ink = Color(0xff34272e);
const _muted = Color(0xff806f78);
const _rose = Color(0xffad4f7d);
const _roseDeep = Color(0xff914263);
const _surface = Color(0xfffff9fc);
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

/// Full-screen account page. The historical class name is retained so existing
/// imports do not churn; it is no longer presented as a bottom sheet.
class AccountAccessSheet extends StatefulWidget {
  const AccountAccessSheet({required this.saved, super.key});

  final SavedConnection? saved;

  @override
  State<AccountAccessSheet> createState() => _AccountAccessSheetState();
}

class _AccountAccessSheetState extends State<AccountAccessSheet> {
  late final TextEditingController _base;
  late final TextEditingController _username;
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
  bool _hideConfirmPassword = true;
  int _resendLeft = 0;
  String _error = '';
  Timer? _resendTimer;

  @override
  void initState() {
    super.initState();
    _base = TextEditingController(
      text: widget.saved?.baseUrl ?? _defaultBaseUrl,
    );
    _username = TextEditingController(text: widget.saved?.accountId ?? '');
    _email = TextEditingController();
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
    _resendTimer?.cancel();
    _base.dispose();
    _username.dispose();
    _email.dispose();
    _password.dispose();
    _confirmPassword.dispose();
    _code.dispose();
    _device.dispose();
    super.dispose();
  }

  Future<void> _loadInitialState() async {
    final consent = await hasPrivacyConsent();
    if (!mounted) {
      return;
    }
    setState(() => _agreed = consent);
    if (consent) {
      unawaited(_loadConfig());
    }
  }

  Future<void> _loadConfig() async {
    try {
      final value = await XiaoyouApi.authConfig(_base.text.trim());
      if (mounted) {
        setState(() => _config = value);
      }
    } catch (_) {
      // Password login remains available when the public config endpoint is
      // temporarily unavailable. Registration/reset will show a maintenance hint.
    }
  }

  Future<void> _setAgreement(bool value) async {
    if (value) {
      await savePrivacyConsent();
    }
    if (!mounted) {
      return;
    }
    setState(() {
      _agreed = value;
      _error = '';
    });
    if (value && _config == null) {
      unawaited(_loadConfig());
    }
  }

  Future<void> _openDocument(String url) async {
    try {
      await openLegalUrl(url);
    } catch (_) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('暂时无法打开页面，请检查网络后重试')),
      );
    }
  }

  void _switchMode(_AccountMode mode) {
    _resendTimer?.cancel();
    setState(() {
      _mode = mode;
      _error = '';
      _busy = false;
      _codeSent = false;
      _resendLeft = 0;
      _code.clear();
      _confirmPassword.clear();
      _password.clear();
      _hidePassword = true;
      _hideConfirmPassword = true;
    });
  }

  bool _ensureConsent() {
    if (_agreed) {
      return true;
    }
    setState(() => _error = '请先阅读并同意《隐私政策》与《用户协议》');
    return false;
  }

  bool _validateBase() {
    if (!_base.text.trim().startsWith('https://') ||
        _device.text.trim().isEmpty) {
      setState(() => _error = '登录服务暂时不可用，请稍后重试');
      return false;
    }
    return true;
  }

  bool _validateUsername() {
    final value = _username.text.trim().toLowerCase();
    if (!RegExp(r'^[a-z0-9][a-z0-9_.-]{2,31}$').hasMatch(value)) {
      setState(() => _error = '账号需为 3–32 位字母、数字、点、横线或下划线');
      return false;
    }
    return true;
  }

  bool _validatePassword({bool confirm = false}) {
    final minLength = _config?.passwordMinLength ?? 8;
    if (_password.text.length < minLength) {
      setState(() => _error = '密码至少 $minLength 位');
      return false;
    }
    if (confirm && _password.text != _confirmPassword.text) {
      setState(() => _error = '两次输入的密码不一致');
      return false;
    }
    return true;
  }

  bool _validateEmail() {
    final value = _email.text.trim();
    if (!RegExp(r'^[^\s@]+@[^\s@]+\.[^\s@]+$').hasMatch(value)) {
      setState(() => _error = '请输入有效的邮箱地址');
      return false;
    }
    return true;
  }

  Future<void> _submit() async {
    if (_busy || !_ensureConsent() || !_validateBase()) {
      return;
    }
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      switch (_mode) {
        case _AccountMode.login:
          await _login();
          break;
        case _AccountMode.register:
          await _register();
          break;
        case _AccountMode.reset:
          await _resetPassword();
          break;
      }
    } catch (error) {
      if (mounted) {
        setState(() => _error = _friendly(error));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _login() async {
    if (!_validateUsername() || !_validatePassword()) {
      return;
    }
    final login = await XiaoyouApi.login(
      baseUrl: _base.text.trim(),
      username: _username.text.trim(),
      password: _password.text,
      deviceId: _device.text.trim(),
      remember: _remember,
    );
    _finish(login);
  }

  Future<void> _register() async {
    if (!_validateUsername() ||
        !_validateEmail() ||
        !_validatePassword(confirm: true)) {
      return;
    }
    if (_config?.registrationEnabled == false) {
      throw StateError('email_service_unavailable');
    }
    if (!_codeSent) {
      final response = await XiaoyouApi.requestRegistration(
        baseUrl: _base.text.trim(),
        username: _username.text.trim(),
        email: _email.text.trim(),
        password: _password.text,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _codeSent = true;
        final debugCode = '${response['debug_code'] ?? ''}'.trim();
        if (debugCode.isNotEmpty) {
          _code.text = debugCode;
        }
      });
      _startResendCooldown();
      return;
    }
    if (_code.text.trim().length != 6) {
      throw const FormatException('invalid_or_expired_code');
    }
    final login = await XiaoyouApi.verifyRegistration(
      baseUrl: _base.text.trim(),
      username: _username.text.trim(),
      email: _email.text.trim(),
      code: _code.text.trim(),
      deviceId: _device.text.trim(),
      remember: _remember,
    );
    _finish(login);
  }

  Future<void> _resetPassword() async {
    final identifier = _username.text.trim();
    if (identifier.isEmpty) {
      setState(() => _error = '请输入账号或绑定邮箱');
      return;
    }
    if (_config?.passwordResetEnabled == false) {
      throw StateError('email_service_unavailable');
    }
    if (!_codeSent) {
      final response = await XiaoyouApi.requestPasswordReset(
        baseUrl: _base.text.trim(),
        identifier: identifier,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _codeSent = true;
        final debugCode = '${response['debug_code'] ?? ''}'.trim();
        if (debugCode.isNotEmpty) {
          _code.text = debugCode;
        }
      });
      _startResendCooldown();
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('如果账号存在，验证码已发送到绑定邮箱')),
      );
      return;
    }
    if (_code.text.trim().length != 6 || !_validatePassword(confirm: true)) {
      return;
    }
    await XiaoyouApi.confirmPasswordReset(
      baseUrl: _base.text.trim(),
      identifier: identifier,
      code: _code.text.trim(),
      password: _password.text,
    );
    if (!mounted) {
      return;
    }
    final loginName = identifier.contains('@') ? '' : identifier;
    _switchMode(_AccountMode.login);
    if (loginName.isNotEmpty) {
      _username.text = loginName;
    }
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('密码已更新，请使用新密码登录')),
    );
  }

  Future<void> _resendCode() async {
    if (_busy || _resendLeft > 0) {
      return;
    }
    setState(() {
      _codeSent = false;
      _code.clear();
    });
    await _submit();
  }

  void _startResendCooldown() {
    _resendTimer?.cancel();
    final seconds = (_config?.resendInterval ?? 60).clamp(1, 300).toInt();
    setState(() => _resendLeft = seconds);
    _resendTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      if (_resendLeft <= 1) {
        timer.cancel();
        setState(() => _resendLeft = 0);
      } else {
        setState(() => _resendLeft -= 1);
      }
    });
  }

  void _finish(XiaoyouLoginResult login) {
    if (!mounted) {
      return;
    }
    Navigator.of(context).pop(
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
    if (value.contains('invalid_credentials')) {
      return '账号或密码不正确';
    }
    if (value.contains('invalid_username')) {
      return '账号格式不正确';
    }
    if (value.contains('username_taken')) {
      return '这个账号已经被使用';
    }
    if (value.contains('invalid_email')) {
      return '请输入有效的邮箱地址';
    }
    if (value.contains('email_already_registered')) {
      return '这个邮箱已经绑定其他账号';
    }
    if (value.contains('invalid_password_length')) {
      return '密码至少 8 位，且不要超过 72 字节';
    }
    if (value.contains('registration_mismatch')) {
      return '注册信息已变化，请重新获取验证码';
    }
    if (value.contains('invalid_or_expired_code')) {
      return '验证码错误或已过期，请重新获取';
    }
    if (value.contains('email_code_too_frequent')) {
      return '验证码刚刚已经发送，请稍后再获取';
    }
    if (value.contains('account_disabled')) {
      return '这个账号当前不可用';
    }
    if (value.contains('email_service_unavailable')) {
      return '邮箱验证服务暂不可用，请稍后再试';
    }
    if (value.contains('auth_service_unavailable')) {
      return '账号服务正在维护，请稍后再试';
    }
    return '暂时无法完成操作，请检查网络后重试';
  }

  String get _title => switch (_mode) {
        _AccountMode.login => '登录小悠',
        _AccountMode.register => '创建账号',
        _AccountMode.reset => '找回密码',
      };

  String get _subtitle => switch (_mode) {
        _AccountMode.login => '使用账号与密码登录，继续只属于你的故事',
        _AccountMode.register => _codeSent
            ? '验证码已发送到 ${_email.text.trim()}，10 分钟内有效'
            : '设置账号与密码，邮箱只用于验证和找回密码',
        _AccountMode.reset => _codeSent ? '输入邮箱验证码并设置新密码' : '验证绑定邮箱后即可重置密码',
      };

  @override
  Widget build(BuildContext context) {
    final canLeave = widget.saved != null;
    return PopScope(
      canPop: canLeave,
      child: Scaffold(
        backgroundColor: _surface,
        body: Stack(
          children: [
            const Positioned.fill(child: _LoginBackdrop()),
            SafeArea(
              child: Column(
                children: [
                  SizedBox(
                    height: 52,
                    child: Row(
                      children: [
                        if (_mode != _AccountMode.login || canLeave)
                          IconButton(
                            tooltip: '返回',
                            onPressed: () {
                              if (_mode != _AccountMode.login) {
                                _switchMode(_AccountMode.login);
                              } else {
                                Navigator.of(context).maybePop();
                              }
                            },
                            icon: const Icon(Icons.arrow_back_ios_new_rounded),
                          )
                        else
                          const SizedBox(width: 48),
                        const Spacer(),
                        const Text(
                          '小悠',
                          style: TextStyle(
                            color: _ink,
                            fontSize: 15,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        const Spacer(),
                        const SizedBox(width: 48),
                      ],
                    ),
                  ),
                  Expanded(
                    child: SingleChildScrollView(
                      keyboardDismissBehavior:
                          ScrollViewKeyboardDismissBehavior.onDrag,
                      padding: const EdgeInsets.fromLTRB(24, 22, 24, 30),
                      child: Center(
                        child: ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 440),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              _LoginHeader(title: _title, subtitle: _subtitle),
                              const SizedBox(height: 34),
                              _modeSwitcher(),
                              const SizedBox(height: 26),
                              ..._buildFields(),
                              if (_error.isNotEmpty) ...[
                                const SizedBox(height: 12),
                                _InlineNotice(text: _error, error: true),
                              ],
                              const SizedBox(height: 18),
                              if (_mode != _AccountMode.reset)
                                _RememberRow(
                                  value: _remember,
                                  onChanged: _busy
                                      ? null
                                      : (value) =>
                                          setState(() => _remember = value),
                                ),
                              const SizedBox(height: 12),
                              _AgreementRow(
                                value: _agreed,
                                onChanged: _busy
                                    ? null
                                    : (value) {
                                        unawaited(_setAgreement(value));
                                      },
                                onPrivacy: () => _openDocument(
                                  xiaoyouPrivacyPolicyUrl,
                                ),
                                onTerms: () => _openDocument(
                                  xiaoyouUserAgreementUrl,
                                ),
                              ),
                              const SizedBox(height: 24),
                              _primaryButton(),
                              const SizedBox(height: 16),
                              _secondaryActions(),
                              const SizedBox(height: 24),
                              Text(
                                _mode == _AccountMode.login
                                    ? '登录后，每个普通用户拥有独立的数据空间；邮箱不会作为公开账号展示。'
                                    : '邮箱仅用于注册验证与账号找回，不作为日常登录凭据。',
                                textAlign: TextAlign.center,
                                style: const TextStyle(
                                  color: Color(0xff9b8992),
                                  fontSize: 11.5,
                                  height: 1.5,
                                ),
                              ),
                            ],
                          ),
                        ),
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

  Widget _modeSwitcher() {
    return Container(
      height: 44,
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.62),
        borderRadius: BorderRadius.circular(15),
        border: Border.all(color: const Color(0xfff0e1e8)),
      ),
      child: Row(
        children: [
          _ModeButton(
            label: '登录',
            selected: _mode == _AccountMode.login,
            onTap: () => _switchMode(_AccountMode.login),
          ),
          _ModeButton(
            label: '注册',
            selected: _mode == _AccountMode.register,
            onTap: () => _switchMode(_AccountMode.register),
          ),
        ],
      ),
    );
  }

  List<Widget> _buildFields() {
    if (_mode == _AccountMode.reset) {
      return [
        _field(
          controller: _username,
          label: '账号或绑定邮箱',
          icon: Icons.person_search_outlined,
          textInputAction:
              _codeSent ? TextInputAction.next : TextInputAction.done,
        ),
        if (_codeSent) ...[
          const SizedBox(height: 14),
          _codeField(),
          const SizedBox(height: 14),
          _passwordField(label: '新密码'),
          const SizedBox(height: 14),
          _passwordField(label: '确认新密码', confirm: true),
        ],
      ];
    }

    return [
      _field(
        controller: _username,
        label: '账号',
        icon: Icons.person_outline_rounded,
        keyboardType: TextInputType.text,
        textInputAction: TextInputAction.next,
        inputFormatters: [
          FilteringTextInputFormatter.allow(RegExp(r'[A-Za-z0-9_.-]')),
          LengthLimitingTextInputFormatter(32),
        ],
      ),
      const SizedBox(height: 14),
      _passwordField(label: '密码'),
      if (_mode == _AccountMode.register) ...[
        const SizedBox(height: 14),
        _passwordField(label: '确认密码', confirm: true),
        const SizedBox(height: 14),
        _field(
          controller: _email,
          label: '邮箱（仅用于验证与找回）',
          icon: Icons.mail_outline_rounded,
          keyboardType: TextInputType.emailAddress,
          textInputAction:
              _codeSent ? TextInputAction.next : TextInputAction.done,
        ),
        if (_codeSent) ...[
          const SizedBox(height: 14),
          _codeField(),
        ],
        if (_config?.registrationEnabled == false) ...[
          const SizedBox(height: 12),
          const _InlineNotice(text: '邮箱验证服务正在维护，暂时无法创建新账号'),
        ],
      ],
    ];
  }

  Widget _passwordField({required String label, bool confirm = false}) {
    final hidden = confirm ? _hideConfirmPassword : _hidePassword;
    return _field(
      controller: confirm ? _confirmPassword : _password,
      label: label,
      icon: Icons.lock_outline_rounded,
      obscureText: hidden,
      textInputAction: TextInputAction.next,
      suffix: IconButton(
        tooltip: hidden ? '显示密码' : '隐藏密码',
        onPressed: () => setState(() {
          if (confirm) {
            _hideConfirmPassword = !_hideConfirmPassword;
          } else {
            _hidePassword = !_hidePassword;
          }
        }),
        icon: Icon(
          hidden ? Icons.visibility_off_outlined : Icons.visibility_outlined,
          color: _muted,
        ),
      ),
    );
  }

  Widget _codeField() {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: _field(
            controller: _code,
            label: '6 位邮箱验证码',
            icon: Icons.verified_user_outlined,
            keyboardType: TextInputType.number,
            textInputAction: TextInputAction.done,
            inputFormatters: [
              FilteringTextInputFormatter.digitsOnly,
              LengthLimitingTextInputFormatter(6),
            ],
          ),
        ),
        const SizedBox(width: 10),
        SizedBox(
          height: 58,
          child: TextButton(
            onPressed: _busy || _resendLeft > 0 ? null : _resendCode,
            child: Text(_resendLeft > 0 ? '${_resendLeft}s' : '重新获取'),
          ),
        ),
      ],
    );
  }

  Widget _field({
    required TextEditingController controller,
    required String label,
    required IconData icon,
    TextInputType? keyboardType,
    TextInputAction? textInputAction,
    bool obscureText = false,
    Widget? suffix,
    List<TextInputFormatter>? inputFormatters,
  }) {
    return TextField(
      controller: controller,
      enabled: !_busy,
      keyboardType: keyboardType,
      textInputAction: textInputAction,
      obscureText: obscureText,
      autocorrect: false,
      enableSuggestions: !obscureText,
      inputFormatters: inputFormatters,
      onSubmitted: (_) {
        if (textInputAction == TextInputAction.done) {
          unawaited(_submit());
        }
      },
      decoration: InputDecoration(
        labelText: label,
        labelStyle: const TextStyle(color: _muted),
        prefixIcon: Icon(icon, color: _rose),
        suffixIcon: suffix,
        filled: true,
        fillColor: Colors.white.withValues(alpha: 0.76),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(18),
          borderSide: const BorderSide(color: Color(0xffeadce4)),
        ),
        disabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(18),
          borderSide: const BorderSide(color: Color(0xffeee4e9)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(18),
          borderSide: const BorderSide(color: _rose, width: 1.4),
        ),
      ),
    );
  }

  Widget _primaryButton() {
    final label = switch (_mode) {
      _AccountMode.login => '登录',
      _AccountMode.register => _codeSent ? '验证并创建账号' : '获取邮箱验证码',
      _AccountMode.reset => _codeSent ? '验证并重置密码' : '获取邮箱验证码',
    };
    return AnimatedOpacity(
      opacity: _agreed ? 1 : 0.58,
      duration: const Duration(milliseconds: 180),
      child: SizedBox(
        height: 54,
        child: FilledButton(
          onPressed: _busy ? null : _submit,
          style: FilledButton.styleFrom(
            backgroundColor: _rose,
            disabledBackgroundColor: _rose.withValues(alpha: 0.55),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(18),
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
                  label,
                  style: const TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w700,
                  ),
                ),
        ),
      ),
    );
  }

  Widget _secondaryActions() {
    if (_mode == _AccountMode.reset) {
      return Center(
        child: TextButton(
          onPressed: _busy ? null : () => _switchMode(_AccountMode.login),
          child: const Text('返回账号登录'),
        ),
      );
    }
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        if (_mode == _AccountMode.login) ...[
          TextButton(
            onPressed: _busy ? null : () => _switchMode(_AccountMode.register),
            child: const Text('没有账号？注册'),
          ),
          const SizedBox(width: 10),
          Container(width: 1, height: 14, color: const Color(0xffdfcfd7)),
          const SizedBox(width: 10),
          TextButton(
            onPressed: _busy ? null : () => _switchMode(_AccountMode.reset),
            child: const Text('忘记密码'),
          ),
        ] else
          TextButton(
            onPressed: _busy ? null : () => _switchMode(_AccountMode.login),
            child: const Text('已有账号？返回登录'),
          ),
      ],
    );
  }
}

class _LoginBackdrop extends StatelessWidget {
  const _LoginBackdrop();

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0xfffff9fc), Color(0xfffff2f7), Color(0xfff7edf4)],
        ),
      ),
      child: Stack(
        children: [
          Positioned(
            right: -70,
            top: -55,
            child: Container(
              width: 220,
              height: 220,
              decoration: const BoxDecoration(
                shape: BoxShape.circle,
                color: Color(0x24bf7fa0),
              ),
            ),
          ),
          Positioned(
            left: -90,
            bottom: 70,
            child: Container(
              width: 240,
              height: 240,
              decoration: const BoxDecoration(
                shape: BoxShape.circle,
                color: Color(0x18a96792),
              ),
            ),
          ),
        ],
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
              width: 94,
              height: 94,
              padding: const EdgeInsets.all(4),
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: Colors.white,
                border: Border.all(color: const Color(0xffffd7e6)),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x2aa64c78),
                    blurRadius: 30,
                    offset: Offset(0, 14),
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
              right: 1,
              bottom: 7,
              child: Container(
                width: 19,
                height: 19,
                decoration: BoxDecoration(
                  color: const Color(0xff36b88b),
                  shape: BoxShape.circle,
                  border: Border.all(color: Colors.white, width: 3),
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 22),
        Text(
          title,
          style: const TextStyle(
            color: _ink,
            fontSize: 28,
            height: 1.1,
            fontWeight: FontWeight.w800,
          ),
        ),
        const SizedBox(height: 9),
        Text(
          subtitle,
          textAlign: TextAlign.center,
          style: const TextStyle(color: _muted, fontSize: 13, height: 1.55),
        ),
      ],
    );
  }
}

class _ModeButton extends StatelessWidget {
  const _ModeButton({
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
      child: Material(
        color: selected ? Colors.white : Colors.transparent,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: onTap,
          child: Center(
            child: Text(
              label,
              style: TextStyle(
                color: selected ? _roseDeep : _muted,
                fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _RememberRow extends StatelessWidget {
  const _RememberRow({required this.value, required this.onChanged});

  final bool value;
  final ValueChanged<bool>? onChanged;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      borderRadius: BorderRadius.circular(10),
      onTap: onChanged == null ? null : () => onChanged!(!value),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(
          children: [
            Checkbox(
              value: value,
              onChanged: onChanged == null
                  ? null
                  : (next) => onChanged!(next ?? false),
              activeColor: _rose,
              visualDensity: VisualDensity.compact,
            ),
            const Text('记住登录', style: TextStyle(color: _muted, fontSize: 13)),
            const Spacer(),
            const Icon(Icons.shield_outlined,
                size: 16, color: Color(0xffaa959f)),
            const SizedBox(width: 5),
            const Text(
              '密码不会明文保存',
              style: TextStyle(color: Color(0xffaa959f), fontSize: 11.5),
            ),
          ],
        ),
      ),
    );
  }
}

class _AgreementRow extends StatelessWidget {
  const _AgreementRow({
    required this.value,
    required this.onChanged,
    required this.onPrivacy,
    required this.onTerms,
  });

  final bool value;
  final ValueChanged<bool>? onChanged;
  final VoidCallback onPrivacy;
  final VoidCallback onTerms;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(top: 1),
          child: Checkbox(
            value: value,
            onChanged:
                onChanged == null ? null : (next) => onChanged!(next ?? false),
            activeColor: _rose,
            visualDensity: VisualDensity.compact,
          ),
        ),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.only(top: 9),
            child: Wrap(
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                const Text(
                  '已阅读并同意 ',
                  style: TextStyle(color: _muted, fontSize: 12),
                ),
                GestureDetector(
                  onTap: onPrivacy,
                  child: const Text(
                    '《隐私政策》',
                    style: TextStyle(color: _rose, fontSize: 12),
                  ),
                ),
                const Text(' 与 ',
                    style: TextStyle(color: _muted, fontSize: 12)),
                GestureDetector(
                  onTap: onTerms,
                  child: const Text(
                    '《用户协议》',
                    style: TextStyle(color: _rose, fontSize: 12),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _InlineNotice extends StatelessWidget {
  const _InlineNotice({required this.text, this.error = false});

  final String text;
  final bool error;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
      decoration: BoxDecoration(
        color: error ? const Color(0xffffedf2) : const Color(0xfffff4f8),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: error ? const Color(0xffffcbd9) : const Color(0xffffdce8),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            error ? Icons.error_outline_rounded : Icons.info_outline_rounded,
            size: 17,
            color: error ? const Color(0xffc84670) : _rose,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              text,
              style: TextStyle(
                color: error ? const Color(0xff9e385b) : _muted,
                fontSize: 12.5,
                height: 1.4,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

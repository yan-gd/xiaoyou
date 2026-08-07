import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'legal.dart';
import 'session_store.dart';
import 'xiaoyou_api.dart';

const _ink = Colors.white;
const _muted = Color(0xffded9ff);
const _rose = Color(0xff6c5cff);
const _roseDeep = Color(0xff5140e8);
const _surface = Color(0xff5e50e9);
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
    final value = _username.text.trim();
    final valid = RegExp(
      r'^[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9][\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9_.-]{1,31}$',
    ).hasMatch(value);
    if (!valid) {
      setState(() => _error = '账号需为 2–32 位，可使用中文、字母、数字及 . _ -');
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
    if (!_validateEmail() ||
        !_validateUsername() ||
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
        resizeToAvoidBottomInset: true,
        body: GestureDetector(
          behavior: HitTestBehavior.translucent,
          onTap: () => FocusManager.instance.primaryFocus?.unfocus(),
          child: Stack(
            children: [
              const Positioned.fill(child: _LoginBackdrop()),
              SafeArea(
                child: LayoutBuilder(
                  builder: (context, viewport) => SingleChildScrollView(
                    keyboardDismissBehavior:
                        ScrollViewKeyboardDismissBehavior.onDrag,
                    padding: const EdgeInsets.fromLTRB(28, 6, 28, 24),
                    child: ConstrainedBox(
                      constraints: BoxConstraints(
                        minHeight: math.max(0.0, viewport.maxHeight - 30),
                      ),
                      child: Center(
                        child: ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 430),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              SizedBox(
                                height: 42,
                                child: Align(
                                  alignment: Alignment.centerLeft,
                                  child: (_mode != _AccountMode.login ||
                                          canLeave)
                                      ? IconButton(
                                          tooltip: '返回',
                                          color: Colors.white,
                                          onPressed: () {
                                            if (_mode != _AccountMode.login) {
                                              _switchMode(_AccountMode.login);
                                            } else {
                                              Navigator.of(context).maybePop();
                                            }
                                          },
                                          icon: const Icon(
                                            Icons.arrow_back_ios_new_rounded,
                                            size: 20,
                                          ),
                                        )
                                      : const SizedBox.shrink(),
                                ),
                              ),
                              _LoginHeader(
                                title: _title,
                                subtitle: _subtitle,
                                compact: _mode == _AccountMode.reset,
                              ),
                              SizedBox(
                                height: _mode == _AccountMode.reset ? 26 : 34,
                              ),
                              if (_mode != _AccountMode.reset) ...[
                                _modeSwitcher(),
                                const SizedBox(height: 26),
                              ],
                              AnimatedSwitcher(
                                duration: const Duration(milliseconds: 260),
                                switchInCurve: Curves.easeOutCubic,
                                switchOutCurve: Curves.easeInCubic,
                                transitionBuilder: (child, animation) =>
                                    FadeTransition(
                                  opacity: animation,
                                  child: SlideTransition(
                                    position: Tween<Offset>(
                                      begin: const Offset(0.04, 0),
                                      end: Offset.zero,
                                    ).animate(animation),
                                    child: child,
                                  ),
                                ),
                                child: Column(
                                  key: ValueKey(_mode),
                                  crossAxisAlignment:
                                      CrossAxisAlignment.stretch,
                                  children: _buildFields(),
                                ),
                              ),
                              if (_error.isNotEmpty) ...[
                                const SizedBox(height: 12),
                                _InlineNotice(text: _error, error: true),
                              ],
                              const SizedBox(height: 10),
                              if (_mode != _AccountMode.reset)
                                _RememberRow(
                                  value: _remember,
                                  onChanged: _busy
                                      ? null
                                      : (value) =>
                                          setState(() => _remember = value),
                                  onForgot: _mode == _AccountMode.login &&
                                          !_busy
                                      ? () => _switchMode(_AccountMode.reset)
                                      : null,
                                ),
                              const SizedBox(height: 12),
                              _primaryButton(),
                              const SizedBox(height: 8),
                              _secondaryActions(),
                              const SizedBox(height: 14),
                              _AgreementRow(
                                value: _agreed,
                                onChanged: _busy
                                    ? null
                                    : (value) =>
                                        unawaited(_setAgreement(value)),
                                onPrivacy: () =>
                                    _openDocument(xiaoyouPrivacyPolicyUrl),
                                onTerms: () =>
                                    _openDocument(xiaoyouUserAgreementUrl),
                              ),
                              const SizedBox(height: 14),
                              Text(
                                _mode == _AccountMode.login
                                    ? '每个账号拥有独立的数据与记忆空间'
                                    : '邮箱仅用于注册验证与找回密码',
                                textAlign: TextAlign.center,
                                style: TextStyle(
                                  color: Colors.white.withValues(alpha: 0.58),
                                  fontSize: 11.5,
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
            ],
          ),
        ),
      ),
    );
  }

  Widget _modeSwitcher() {
    return SizedBox(
      height: 52,
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          _ModeButton(
            label: '登录',
            selected: _mode == _AccountMode.login,
            onTap: () => _switchMode(_AccountMode.login),
          ),
          Container(
            width: 1,
            height: 22,
            margin: const EdgeInsets.symmetric(horizontal: 34),
            color: Colors.white.withValues(alpha: 0.24),
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

    if (_mode == _AccountMode.register) {
      return [
        _field(
          controller: _email,
          label: '邮箱',
          icon: Icons.alternate_email_rounded,
          keyboardType: TextInputType.emailAddress,
          textInputAction: TextInputAction.next,
        ),
        const SizedBox(height: 14),
        _field(
          controller: _username,
          label: '账号名字（可使用中文且不可重复）',
          icon: Icons.person_outline_rounded,
          keyboardType: TextInputType.text,
          textInputAction: TextInputAction.next,
          inputFormatters: [LengthLimitingTextInputFormatter(32)],
        ),
        const SizedBox(height: 14),
        _passwordField(label: '密码'),
        const SizedBox(height: 14),
        _passwordField(label: '确认密码', confirm: true),
        if (_codeSent) ...[
          const SizedBox(height: 14),
          _codeField(),
        ],
        if (_config?.registrationEnabled == false) ...[
          const SizedBox(height: 12),
          const _InlineNotice(text: '邮箱验证服务正在维护，暂时无法创建新账号'),
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
        inputFormatters: [LengthLimitingTextInputFormatter(32)],
      ),
      const SizedBox(height: 14),
      _passwordField(label: '密码'),
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
            style: TextButton.styleFrom(
              foregroundColor: Colors.white,
              disabledForegroundColor: Colors.white54,
            ),
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
        hintText: label,
        hintStyle: TextStyle(
          color: Colors.white.withValues(alpha: 0.68),
          fontSize: 15,
        ),
        prefixIcon: Icon(icon, color: Colors.white, size: 21),
        suffixIcon: suffix,
        filled: true,
        fillColor: Colors.white.withValues(alpha: 0.13),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(22),
          borderSide: BorderSide(
            color: Colors.white.withValues(alpha: 0.2),
          ),
        ),
        disabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(22),
          borderSide: BorderSide(
            color: Colors.white.withValues(alpha: 0.12),
          ),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(22),
          borderSide: BorderSide(
            color: Colors.white.withValues(alpha: 0.92),
            width: 1.3,
          ),
        ),
      ),
      cursorColor: Colors.white,
      style: const TextStyle(color: Colors.white, fontSize: 15.5),
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
        height: 58,
        child: FilledButton(
          onPressed: _busy ? null : _submit,
          style: FilledButton.styleFrom(
            foregroundColor: _roseDeep,
            backgroundColor: Colors.white,
            disabledForegroundColor: _roseDeep.withValues(alpha: 0.55),
            disabledBackgroundColor: Colors.white.withValues(alpha: 0.72),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(29),
            ),
            elevation: 0,
          ),
          child: _busy
              ? const SizedBox(
                  width: 22,
                  height: 22,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: _roseDeep,
                  ),
                )
              : Text(
                  label,
                  style: const TextStyle(
                    fontSize: 17,
                    fontWeight: FontWeight.w800,
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
          style: TextButton.styleFrom(foregroundColor: Colors.white70),
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
            style: TextButton.styleFrom(foregroundColor: Colors.white70),
            child: const Text('还没有账号？去注册'),
          ),
        ] else
          TextButton(
            onPressed: _busy ? null : () => _switchMode(_AccountMode.login),
            style: TextButton.styleFrom(foregroundColor: Colors.white70),
            child: const Text('已有账号？返回登录'),
          ),
      ],
    );
  }
}

class _LoginBackdrop extends StatefulWidget {
  const _LoginBackdrop();

  @override
  State<_LoginBackdrop> createState() => _LoginBackdropState();
}

class _LoginBackdropState extends State<_LoginBackdrop>
    with SingleTickerProviderStateMixin {
  late final AnimationController _motion;

  @override
  void initState() {
    super.initState();
    _motion = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 9),
    )..repeat();
  }

  @override
  void dispose() {
    _motion.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _motion,
      builder: (context, _) {
        final phase = _motion.value * math.pi * 2;
        return DecoratedBox(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [
                Color(0xff5045ef),
                Color(0xff7964f4),
                Color(0xffa58af4),
              ],
              stops: [0, 0.56, 1],
            ),
          ),
          child: Stack(
            children: [
              Positioned(
                left: -90 + math.sin(phase) * 22,
                top: 70 + math.cos(phase) * 18,
                child: const _GlowOrb(
                  size: 310,
                  color: Color(0x3a4c4cff),
                ),
              ),
              Positioned(
                right: -105 + math.cos(phase) * 26,
                top: 245 + math.sin(phase) * 24,
                child: const _GlowOrb(
                  size: 330,
                  color: Color(0x46e7cfff),
                ),
              ),
              Positioned(
                left: 42,
                bottom: 40 + math.sin(phase) * 16,
                child: const _GlowOrb(
                  size: 190,
                  color: Color(0x24bcecff),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _GlowOrb extends StatelessWidget {
  const _GlowOrb({required this.size, required this.color});

  final double size;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: RadialGradient(
          colors: [color, color.withValues(alpha: 0)],
        ),
      ),
    );
  }
}

class _LoginHeader extends StatelessWidget {
  const _LoginHeader({
    required this.title,
    required this.subtitle,
    required this.compact,
  });

  final String title;
  final String subtitle;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        if (!compact) ...[
          const _BrandWordmark(),
          const SizedBox(height: 12),
          Text(
            '你的专属 AI 陪伴',
            style: TextStyle(
              color: Colors.white.withValues(alpha: 0.82),
              fontSize: 15,
              letterSpacing: 1.6,
            ),
          ),
        ] else ...[
          Text(
            title,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 30,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            subtitle,
            textAlign: TextAlign.center,
            style: const TextStyle(color: _muted, fontSize: 13, height: 1.5),
          ),
        ],
      ],
    );
  }
}

class _BrandWordmark extends StatelessWidget {
  const _BrandWordmark();

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 240,
      height: 105,
      child: Stack(
        alignment: Alignment.center,
        children: [
          Positioned.fill(
            child: CustomPaint(painter: _BrandOrbitPainter()),
          ),
          const Text(
            '小 悠',
            style: TextStyle(
              color: Colors.white,
              fontSize: 58,
              height: 1,
              fontWeight: FontWeight.w800,
              letterSpacing: 7,
              shadows: [
                Shadow(color: Color(0x66524af0), blurRadius: 28),
              ],
            ),
          ),
          const Positioned(
            right: 35,
            top: 24,
            child: Icon(Icons.favorite_rounded, color: Colors.white, size: 12),
          ),
          const Positioned(
            left: 42,
            top: 4,
            child: Icon(Icons.auto_awesome_rounded,
                color: Color(0xffe5dcff), size: 20),
          ),
        ],
      ),
    );
  }
}

class _BrandOrbitPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.2
      ..shader = const LinearGradient(
        colors: [Color(0x08ffffff), Color(0xccffffff), Color(0x16ffffff)],
      ).createShader(Offset.zero & size);
    final path = Path()
      ..moveTo(22, size.height * 0.72)
      ..cubicTo(size.width * 0.35, size.height * 1.02, size.width * 0.93,
          size.height * 0.83, size.width - 25, size.height * 0.41);
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
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
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(
              label,
              style: TextStyle(
                color: selected ? Colors.white : Colors.white60,
                fontSize: 18,
                fontWeight: selected ? FontWeight.w800 : FontWeight.w500,
              ),
            ),
            const SizedBox(height: 8),
            AnimatedContainer(
              duration: const Duration(milliseconds: 220),
              curve: Curves.easeOutCubic,
              width: selected ? 34 : 0,
              height: 3,
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _RememberRow extends StatelessWidget {
  const _RememberRow({
    required this.value,
    required this.onChanged,
    this.onForgot,
  });

  final bool value;
  final ValueChanged<bool>? onChanged;
  final VoidCallback? onForgot;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: onChanged == null ? null : () => onChanged!(!value),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                AnimatedContainer(
                  duration: const Duration(milliseconds: 180),
                  width: 21,
                  height: 21,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: value ? Colors.white : Colors.transparent,
                    border: Border.all(
                      color: Colors.white.withValues(alpha: value ? 1 : 0.8),
                      width: 1.5,
                    ),
                  ),
                  child: value
                      ? const Icon(Icons.check_rounded,
                          size: 15, color: _roseDeep)
                      : null,
                ),
                const SizedBox(width: 9),
                const Text(
                  '记住登录状态',
                  style: TextStyle(color: Colors.white, fontSize: 13),
                ),
              ],
            ),
          ),
        ),
        const Spacer(),
        if (onForgot != null)
          TextButton(
            onPressed: onForgot,
            style: TextButton.styleFrom(
              foregroundColor: Colors.white,
              padding: const EdgeInsets.symmetric(horizontal: 2),
            ),
            child: const Text('忘记密码？'),
          ),
      ],
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
            activeColor: Colors.white,
            checkColor: _roseDeep,
            side: BorderSide(color: Colors.white.withValues(alpha: 0.82)),
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
                  style: TextStyle(color: Colors.white70, fontSize: 12),
                ),
                GestureDetector(
                  onTap: onPrivacy,
                  child: const Text(
                    '《隐私政策》',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                const Text(' 与 ',
                    style: TextStyle(color: Colors.white70, fontSize: 12)),
                GestureDetector(
                  onTap: onTerms,
                  child: const Text(
                    '《用户协议》',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                    ),
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
        color: error
            ? const Color(0x35ff315f)
            : Colors.white.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: error
              ? const Color(0x88ffb4c5)
              : Colors.white.withValues(alpha: 0.2),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            error ? Icons.error_outline_rounded : Icons.info_outline_rounded,
            size: 17,
            color: Colors.white,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              text,
              style: TextStyle(
                color: Colors.white,
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

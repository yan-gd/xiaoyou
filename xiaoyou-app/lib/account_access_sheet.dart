import 'dart:async';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'legal.dart';
import 'session_store.dart';
import 'xiaoyou_api.dart';

const _violet = Color(0xff8c82f4);
const _violetDeep = Color(0xff7568ef);
const _ink = Color(0xff25243a);
const _muted = Color(0xff9693ad);
const _line = Color(0xffe6e4f2);
const _defaultBaseUrl = 'https://xiaoyou.yoyoyan.cn/xiaoyou-app';
const _loginBackgroundAsset = 'assets/login_background.png';
const _qqAsset = 'assets/QQ.png';
const _emailLoginAsset = 'assets/email-login.png';
const _githubAsset = 'assets/github.png';

enum _AccountMode { login, emailLogin, register, reset }

class AccountAccessResult {
  const AccountAccessResult({
    required this.baseUrl,
    required this.deviceId,
    required this.remember,
    required this.login,
    required this.profile,
    required this.shouldOnboard,
  });

  final String baseUrl;
  final String deviceId;
  final bool remember;
  final XiaoyouLoginResult login;
  final XiaoyouUserProfile? profile;
  final bool? shouldOnboard;
}

class AccountAccessSheet extends StatefulWidget {
  const AccountAccessSheet({required this.saved, super.key});

  final SavedConnection? saved;

  @override
  State<AccountAccessSheet> createState() => _AccountAccessSheetState();
}

class _AccountAccessSheetState extends State<AccountAccessSheet>
    with SingleTickerProviderStateMixin {
  late final TextEditingController _base;
  late final TextEditingController _username;
  late final TextEditingController _email;
  late final TextEditingController _password;
  late final TextEditingController _confirmPassword;
  late final TextEditingController _code;
  late final TextEditingController _device;
  late final AnimationController _entrance;

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
    _entrance = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1050),
    )..forward();
    unawaited(_loadInitialState());
  }

  @override
  void dispose() {
    _resendTimer?.cancel();
    _entrance.dispose();
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
    if (!mounted) return;

    // The authentication-page agreement must always start unchecked. A
    // previously stored privacy consent may unlock config loading, but it must
    // never count as an active consent action for the current login/register
    // attempt.
    setState(() => _agreed = false);
    if (consent) unawaited(_loadConfig());
  }

  Future<void> _loadConfig() async {
    try {
      final value = await XiaoyouApi.authConfig(_base.text.trim());
      if (mounted) setState(() => _config = value);
    } catch (_) {
      // Password login still works while auth-config is temporarily unavailable.
    }
  }

  Future<void> _setAgreement(bool value) async {
    if (value) await savePrivacyConsent();
    if (!mounted) return;
    setState(() {
      _agreed = value;
      _error = '';
    });
    if (value && _config == null) unawaited(_loadConfig());
  }

  Future<void> _openDocument(String url) async {
    try {
      await openLegalUrl(url);
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('暂时无法打开页面，请检查网络后重试')),
      );
    }
  }

  void _switchMode(_AccountMode mode) {
    _resendTimer?.cancel();
    HapticFeedback.selectionClick();
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
    if (_agreed) return true;
    HapticFeedback.mediumImpact();
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
    if (_busy || !_ensureConsent() || !_validateBase()) return;
    FocusManager.instance.primaryFocus?.unfocus();
    HapticFeedback.lightImpact();
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      switch (_mode) {
        case _AccountMode.login:
          await _login();
          break;
        case _AccountMode.emailLogin:
          await _emailCodeLogin();
          break;
        case _AccountMode.register:
          await _register();
          break;
        case _AccountMode.reset:
          await _resetPassword();
          break;
      }
    } catch (error) {
      if (mounted) setState(() => _error = _friendly(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _login() async {
    if (!_validateUsername() || !_validatePassword()) return;
    final login = await XiaoyouApi.login(
      baseUrl: _base.text.trim(),
      username: _username.text.trim(),
      password: _password.text,
      deviceId: _device.text.trim(),
      remember: _remember,
    );
    await _finish(login);
  }

  Future<void> _emailCodeLogin() async {
    if (!_validateEmail()) return;
    if (!_codeSent) {
      final response = await XiaoyouApi.requestEmailLogin(
        baseUrl: _base.text.trim(),
        email: _email.text.trim(),
      );
      if (!mounted) return;
      setState(() {
        _codeSent = true;
        final debugCode = '${response['debug_code'] ?? ''}'.trim();
        if (debugCode.isNotEmpty) _code.text = debugCode;
      });
      _startResendCooldown();
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(
          const SnackBar(
            content: Text('验证码已发送到邮箱，10 分钟内有效'),
            behavior: SnackBarBehavior.floating,
          ),
        );
      return;
    }
    if (_code.text.trim().length != 6) {
      throw const FormatException('invalid_or_expired_code');
    }
    final login = await XiaoyouApi.verifyEmailLogin(
      baseUrl: _base.text.trim(),
      email: _email.text.trim(),
      code: _code.text.trim(),
      deviceId: _device.text.trim(),
      remember: _remember,
    );
    await _finish(login);
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
      if (!mounted) return;
      setState(() {
        _codeSent = true;
        final debugCode = '${response['debug_code'] ?? ''}'.trim();
        if (debugCode.isNotEmpty) _code.text = debugCode;
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
    await _finish(login);
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
      if (!mounted) return;
      setState(() {
        _codeSent = true;
        final debugCode = '${response['debug_code'] ?? ''}'.trim();
        if (debugCode.isNotEmpty) _code.text = debugCode;
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
    if (!mounted) return;
    final loginName = identifier.contains('@') ? '' : identifier;
    _switchMode(_AccountMode.login);
    if (loginName.isNotEmpty) _username.text = loginName;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('密码已更新，请使用新密码登录')),
    );
  }

  Future<void> _resendCode() async {
    if (_busy || _resendLeft > 0) return;
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

  Future<void> _finish(XiaoyouLoginResult login) async {
    XiaoyouUserProfile? profile;
    bool? shouldOnboard;
    final baseUrl = _base.text.trim();
    final source = XiaoyouApi(
      baseUrl: baseUrl,
      token: login.token,
      deviceId: login.deviceId,
      accountId: login.accountId,
      testMode: login.testMode,
    );
    try {
      final fetchedProfile = await source.accountProfile().timeout(
            const Duration(seconds: 4),
          );
      profile = fetchedProfile;
      final normalizedBase = baseUrl.replaceFirst(RegExp(r'/+$'), '');
      final mode = login.testMode ? 'test' : 'user';
      final onboardingKey = '$normalizedBase|$mode|${login.accountId}';
      final store = SessionStore();
      final seen = await store.hasSeenFirstRunOnboarding(onboardingKey);
      final specialAccount = login.testMode || login.accountId == 'yoyo';
      // For normal registered users the server-side profile completion flag is
      // authoritative. A stale local "seen" flag must never suppress unfinished
      // onboarding, including sessions created through email-code login.
      shouldOnboard = specialAccount ? !seen : !fetchedProfile.profileCompleted;
      if (!specialAccount && fetchedProfile.profileCompleted && !seen) {
        await store.markFirstRunOnboardingSeen(onboardingKey);
      }
    } catch (_) {
      profile = null;
      shouldOnboard = null;
    } finally {
      source.close();
    }
    if (!mounted) return;
    HapticFeedback.mediumImpact();
    Navigator.of(context).pop(
      AccountAccessResult(
        baseUrl: baseUrl,
        deviceId: login.deviceId,
        remember: _remember,
        login: login,
        profile: profile,
        shouldOnboard: shouldOnboard,
      ),
    );
  }

  Future<void> _githubLogin() async {
    if (_busy || !_ensureConsent() || !_validateBase()) return;
    if (_config?.githubLoginEnabled == false) {
      setState(() => _error = 'GitHub 登录暂未配置');
      return;
    }
    FocusManager.instance.primaryFocus?.unfocus();
    HapticFeedback.selectionClick();
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      final flow = await XiaoyouApi.startOAuth(
        baseUrl: _base.text.trim(),
        provider: 'github',
        deviceId: _device.text.trim(),
        remember: _remember,
      );
      await openLegalUrl(flow.authorizationUrl);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(
          const SnackBar(
            content: Text('已打开 GitHub 授权页面，完成后返回小悠即可'),
            behavior: SnackBarBehavior.floating,
          ),
        );
      final deadline = DateTime.now().add(
        Duration(seconds: flow.expiresIn.clamp(30, 600).toInt()),
      );
      while (mounted && DateTime.now().isBefore(deadline)) {
        await Future<void>.delayed(const Duration(milliseconds: 1400));
        if (!mounted) return;
        final login = await XiaoyouApi.pollOAuth(
          baseUrl: _base.text.trim(),
          provider: 'github',
          pollToken: flow.pollToken,
        );
        if (login != null) {
          await _finish(login);
          return;
        }
      }
      throw TimeoutException('oauth_timeout');
    } catch (error) {
      if (mounted) setState(() => _error = _friendly(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _qqLogin() {
    HapticFeedback.selectionClick();
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        const SnackBar(
          content: Text('QQ 登录需要腾讯 App ID 与服务端 OAuth 回调，当前工程尚未配置'),
          behavior: SnackBarBehavior.floating,
        ),
      );
  }

  String _friendly(Object error) {
    final value = '$error';

    if (value.contains('oauth_cancelled')) {
      return '已取消第三方账号授权';
    }
    if (value.contains('oauth_provider_unavailable')) {
      return '这个第三方登录方式暂未配置';
    }
    if (value.contains('oauth_expired') || value.contains('oauth_timeout')) {
      return '登录授权已超时，请重新尝试';
    }
    if (value.contains('oauth_provider_failed') ||
        value.contains('invalid_oauth')) {
      return '第三方登录暂时失败，请稍后重试';
    }

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
    if (value.contains('email_request_rate_limited')) {
      return '验证码请求过于频繁，请稍后再试';
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
        _AccountMode.login => '欢迎回来',
        _AccountMode.emailLogin => '邮箱登录',
        _AccountMode.register => '创建你的账号',
        _AccountMode.reset => '找回密码',
      };

  String get _subtitle => switch (_mode) {
        _AccountMode.login => '继续和小悠聊聊今天吧',
        _AccountMode.emailLogin => _codeSent
            ? '验证码已发送到 ${_email.text.trim()}，10 分钟内有效'
            : '使用绑定邮箱获取验证码，无需输入密码',
        _AccountMode.register =>
          _codeSent ? '验证码已发送到 ${_email.text.trim()}，10 分钟内有效' : '邮箱只用于验证与找回密码',
        _AccountMode.reset => _codeSent ? '输入验证码并设置新密码' : '验证绑定邮箱后即可重置密码',
      };

  @override
  Widget build(BuildContext context) {
    final canLeave = widget.saved != null;
    final keyboardInset = MediaQuery.viewInsetsOf(context).bottom;
    return PopScope(
      canPop: canLeave,
      child: Scaffold(
        resizeToAvoidBottomInset: false,
        backgroundColor: Colors.white,
        body: GestureDetector(
          behavior: HitTestBehavior.translucent,
          onTap: () => FocusManager.instance.primaryFocus?.unfocus(),
          child: LayoutBuilder(
            builder: (context, viewport) {
              final cardTop = viewport.maxHeight * 0.355;
              final keyboardLift = keyboardInset > 0
                  ? (keyboardInset * 0.22).clamp(0.0, 82.0).toDouble()
                  : 0.0;
              return Stack(
                fit: StackFit.expand,
                children: [
                  const _LoginBackdrop(),
                  SafeArea(
                    bottom: false,
                    child: Stack(
                      children: [
                        Positioned(
                          left: 30,
                          top: viewport.maxHeight * 0.085,
                          child: _Entrance(
                            animation: _entrance,
                            interval: const Interval(
                              0.0,
                              0.48,
                              curve: Curves.easeOutCubic,
                            ),
                            offsetY: 14,
                            child: const _BrandHeader(),
                          ),
                        ),
                        if (canLeave)
                          Positioned(
                            left: 12,
                            top: 4,
                            child: _GlassIconButton(
                              icon: Icons.arrow_back_ios_new_rounded,
                              onTap: () => Navigator.of(context).maybePop(),
                            ),
                          ),
                      ],
                    ),
                  ),
                  AnimatedPositioned(
                    duration: const Duration(milliseconds: 300),
                    curve: Curves.easeOutCubic,
                    left: 0,
                    right: 0,
                    top: cardTop - keyboardLift,
                    bottom: 0,
                    child: _Entrance(
                      animation: _entrance,
                      interval: const Interval(
                        0.16,
                        0.86,
                        curve: Curves.easeOutBack,
                      ),
                      offsetY: 30,
                      child: _AuthSheet(
                        child: LayoutBuilder(
                          builder: (context, sheetViewport) {
                            return Align(
                              alignment: Alignment.topCenter,
                              child: FittedBox(
                                fit: BoxFit.scaleDown,
                                alignment: Alignment.topCenter,
                                child: SizedBox(
                                  width: viewport.maxWidth,
                                  child: AnimatedSwitcher(
                                    duration: const Duration(milliseconds: 360),
                                    reverseDuration:
                                        const Duration(milliseconds: 240),
                                    switchInCurve: Curves.easeOutCubic,
                                    switchOutCurve: Curves.easeInCubic,
                                    transitionBuilder: (child, animation) {
                                      final slide = Tween<Offset>(
                                        begin: const Offset(0.025, 0.012),
                                        end: Offset.zero,
                                      ).animate(animation);
                                      return FadeTransition(
                                        opacity: animation,
                                        child: SlideTransition(
                                          position: slide,
                                          child: child,
                                        ),
                                      );
                                    },
                                    child: _buildModeContent(
                                      key: ValueKey('${_mode.name}-$_codeSent'),
                                    ),
                                  ),
                                ),
                              ),
                            );
                          },
                        ),
                      ),
                    ),
                  ),
                ],
              );
            },
          ),
        ),
      ),
    );
  }

  Widget _buildModeContent({required Key key}) {
    final loginMode = _mode == _AccountMode.login;
    final emailLoginMode = _mode == _AccountMode.emailLogin;
    final registerMode = _mode == _AccountMode.register;
    return Padding(
      key: key,
      padding: EdgeInsets.fromLTRB(
        30,
        registerMode ? 22 : 28,
        30,
        18,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      _title,
                      style: const TextStyle(
                        color: _ink,
                        fontSize: 28,
                        height: 1.04,
                        fontWeight: FontWeight.w800,
                        letterSpacing: -0.7,
                      ),
                    ),
                    const SizedBox(height: 7),
                    Text(
                      _subtitle,
                      style: const TextStyle(
                        color: _muted,
                        fontSize: 13.5,
                        height: 1.35,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 14),
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: _InlineModeButton(
                  label: loginMode ? '注册' : (emailLoginMode ? '账号登录' : '登录'),
                  onTap: _busy
                      ? null
                      : () => _switchMode(
                            loginMode
                                ? _AccountMode.register
                                : _AccountMode.login,
                          ),
                ),
              ),
            ],
          ),
          SizedBox(height: registerMode ? 15 : 18),
          ..._buildFields(),
          if (_error.isNotEmpty) ...[
            const SizedBox(height: 8),
            _InlineNotice(text: _error, error: true),
          ],
          if (_mode != _AccountMode.reset) ...[
            const SizedBox(height: 8),
            _RememberRow(
              value: _remember,
              onChanged:
                  _busy ? null : (value) => setState(() => _remember = value),
              onForgot: loginMode && !_busy
                  ? () => _switchMode(_AccountMode.reset)
                  : null,
            ),
          ],
          const SizedBox(height: 12),
          _primaryButton(),
          if (loginMode) ...[
            const SizedBox(height: 18),
            _qqLoginArea(),
          ],
          SizedBox(height: registerMode ? 10 : 13),
          _AgreementRow(
            value: _agreed,
            onChanged:
                _busy ? null : (value) => unawaited(_setAgreement(value)),
            onPrivacy: () => _openDocument(xiaoyouPrivacyPolicyUrl),
            onTerms: () => _openDocument(xiaoyouUserAgreementUrl),
          ),
          if (_mode == _AccountMode.reset) ...[
            const SizedBox(height: 8),
            Center(
              child: _InlineModeButton(
                label: '返回账号登录',
                onTap: _busy ? null : () => _switchMode(_AccountMode.login),
              ),
            ),
          ],
        ],
      ),
    );
  }

  List<Widget> _buildFields() {
    if (_mode == _AccountMode.emailLogin) {
      return [
        _field(
          controller: _email,
          label: '邮箱地址',
          icon: Icons.alternate_email_rounded,
          keyboardType: TextInputType.emailAddress,
          textInputAction:
              _codeSent ? TextInputAction.next : TextInputAction.done,
        ),
        if (_codeSent) ...[
          const SizedBox(height: 12),
          _codeField(),
        ],
      ];
    }

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
          const SizedBox(height: 12),
          _codeField(),
          const SizedBox(height: 12),
          _passwordField(label: '新密码'),
          const SizedBox(height: 12),
          _passwordField(label: '确认新密码', confirm: true),
        ],
      ];
    }

    if (_mode == _AccountMode.register) {
      return [
        _field(
          controller: _email,
          label: '邮箱地址',
          icon: Icons.alternate_email_rounded,
          keyboardType: TextInputType.emailAddress,
          textInputAction: TextInputAction.next,
        ),
        const SizedBox(height: 10),
        _field(
          controller: _username,
          label: '账号名字',
          icon: Icons.person_outline_rounded,
          textInputAction: TextInputAction.next,
          inputFormatters: [LengthLimitingTextInputFormatter(32)],
        ),
        const SizedBox(height: 10),
        _passwordField(label: '密码'),
        const SizedBox(height: 10),
        _passwordField(label: '确认密码', confirm: true),
        if (_codeSent) ...[
          const SizedBox(height: 10),
          _codeField(),
        ],
        if (_config?.registrationEnabled == false) ...[
          const SizedBox(height: 10),
          const _InlineNotice(text: '邮箱验证服务正在维护，暂时无法创建新账号'),
        ],
      ];
    }

    return [
      _field(
        controller: _username,
        label: '账号',
        icon: Icons.person_outline_rounded,
        textInputAction: TextInputAction.next,
        inputFormatters: [LengthLimitingTextInputFormatter(32)],
      ),
      const SizedBox(height: 13),
      _passwordField(label: '请输入密码'),
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
          color: const Color(0xffaaa7bd),
          size: 20,
        ),
      ),
    );
  }

  Widget _codeField() {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
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
        const SizedBox(width: 8),
        SizedBox(
          height: 54,
          child: TextButton(
            onPressed: _busy || _resendLeft > 0 ? null : _resendCode,
            style: TextButton.styleFrom(
              foregroundColor: _violetDeep,
              disabledForegroundColor: const Color(0xffbbb8c8),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(18),
              ),
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
    return SizedBox(
      height: 56,
      child: TextField(
        controller: controller,
        enabled: !_busy,
        keyboardType: keyboardType,
        textInputAction: textInputAction,
        obscureText: obscureText,
        autocorrect: false,
        enableSuggestions: !obscureText,
        inputFormatters: inputFormatters,
        onSubmitted: (_) {
          if (textInputAction == TextInputAction.done) unawaited(_submit());
        },
        cursorColor: _violetDeep,
        style: const TextStyle(
          color: _ink,
          fontSize: 15.5,
          fontWeight: FontWeight.w600,
        ),
        decoration: InputDecoration(
          hintText: label,
          hintStyle: const TextStyle(
            color: Color(0xffaaa7bd),
            fontSize: 15,
            fontWeight: FontWeight.w500,
          ),
          prefixIcon: Icon(icon, color: _violetDeep, size: 21),
          suffixIcon: suffix,
          filled: true,
          fillColor: Colors.white.withValues(alpha: 0.84),
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(28),
            borderSide: const BorderSide(color: _line, width: 1.15),
          ),
          disabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(28),
            borderSide: const BorderSide(color: Color(0xffeeeeF5)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(28),
            borderSide: const BorderSide(color: Color(0xff9d92f7), width: 1.5),
          ),
        ),
      ),
    );
  }

  Widget _primaryButton() {
    final label = switch (_mode) {
      _AccountMode.login => '登录',
      _AccountMode.emailLogin => _codeSent ? '验证并登录' : '获取邮箱验证码',
      _AccountMode.register => _codeSent ? '验证并创建账号' : '获取邮箱验证码',
      _AccountMode.reset => _codeSent ? '验证并重置密码' : '获取邮箱验证码',
    };
    return _BouncyPrimaryButton(
      enabled: !_busy,
      onTap: _submit,
      child: _busy
          ? const SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(
                strokeWidth: 2.2,
                color: Colors.white,
              ),
            )
          : Text(
              label,
              style: const TextStyle(
                color: Colors.white,
                fontSize: 17,
                fontWeight: FontWeight.w800,
                letterSpacing: 0.4,
              ),
            ),
    );
  }

  Widget _qqLoginArea() {
    final githubEnabled = _config?.githubLoginEnabled ?? true;
    return Column(
      children: [
        Row(
          children: [
            const Expanded(child: Divider(color: Color(0xffe8e6f1))),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14),
              child: Text(
                '其他登录方式',
                style: TextStyle(
                  color: _muted.withValues(alpha: 0.9),
                  fontSize: 12.5,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ),
            const Expanded(child: Divider(color: Color(0xffe8e6f1))),
          ],
        ),
        const SizedBox(height: 12),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
          children: [
            _QQButton(onTap: _busy ? null : _qqLogin),
            _EmailLoginButton(
              onTap: _busy ? null : () => _switchMode(_AccountMode.emailLogin),
            ),
            _ProviderLoginButton(
              asset: _githubAsset,
              label: 'GitHub',
              onTap: _busy || !githubEnabled
                  ? null
                  : () => unawaited(_githubLogin()),
            ),
          ],
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
      decoration: const BoxDecoration(color: Colors.white),
      child: Image.asset(
        _loginBackgroundAsset,
        fit: BoxFit.fill,
        filterQuality: FilterQuality.high,
        gaplessPlayback: true,
      ),
    );
  }
}

class _BrandHeader extends StatelessWidget {
  const _BrandHeader();

  @override
  Widget build(BuildContext context) {
    return const Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          '小 悠',
          style: TextStyle(
            color: Color(0xff8d83f3),
            fontFamily: 'serif',
            fontFamilyFallback: ['Noto Serif CJK SC', 'Noto Serif SC', 'serif'],
            fontSize: 56,
            height: 1.02,
            fontWeight: FontWeight.w600,
            letterSpacing: 3.2,
          ),
        ),
        SizedBox(height: 13),
        Text(
          '遇见小悠，遇见更从容的自己 ♡',
          style: TextStyle(
            color: Color(0xff746bd2),
            fontFamily: 'serif',
            fontFamilyFallback: ['Noto Serif CJK SC', 'Noto Serif SC', 'serif'],
            fontSize: 14.5,
            height: 1.35,
            fontWeight: FontWeight.w500,
            letterSpacing: 0.35,
          ),
        ),
      ],
    );
  }
}

class _AuthSheet extends StatelessWidget {
  const _AuthSheet({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: const BorderRadius.vertical(top: Radius.circular(42)),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
        child: Container(
          width: double.infinity,
          height: double.infinity,
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.965),
            borderRadius: const BorderRadius.vertical(top: Radius.circular(42)),
            border: Border(
              top: BorderSide(
                color: Colors.white.withValues(alpha: 0.98),
                width: 1.2,
              ),
            ),
            boxShadow: const [
              BoxShadow(
                color: Color(0x1f746bd2),
                blurRadius: 42,
                offset: Offset(0, -5),
              ),
            ],
          ),
          child: child,
        ),
      ),
    );
  }
}

class _Entrance extends StatelessWidget {
  const _Entrance({
    required this.animation,
    required this.interval,
    required this.offsetY,
    required this.child,
  });

  final Animation<double> animation;
  final Interval interval;
  final double offsetY;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final curved = CurvedAnimation(parent: animation, curve: interval);
    return FadeTransition(
      opacity: curved,
      child: SlideTransition(
        position: Tween<Offset>(
          begin: Offset(0, offsetY / 260),
          end: Offset.zero,
        ).animate(curved),
        child: child,
      ),
    );
  }
}

class _GlassIconButton extends StatelessWidget {
  const _GlassIconButton({required this.icon, required this.onTap});

  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return IconButton(
      onPressed: onTap,
      icon: Icon(icon, color: const Color(0xff5f5877), size: 20),
      style: IconButton.styleFrom(
        backgroundColor: Colors.white.withValues(alpha: 0.66),
        minimumSize: const Size(42, 42),
      ),
    );
  }
}

class _InlineModeButton extends StatelessWidget {
  const _InlineModeButton({required this.label, required this.onTap});

  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return TextButton(
      onPressed: onTap,
      style: TextButton.styleFrom(
        foregroundColor: _violetDeep,
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        minimumSize: const Size(0, 0),
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
      child: Text(
        label,
        style: const TextStyle(
          fontSize: 14,
          fontWeight: FontWeight.w800,
        ),
      ),
    );
  }
}

class _BouncyPrimaryButton extends StatefulWidget {
  const _BouncyPrimaryButton({
    required this.enabled,
    required this.onTap,
    required this.child,
  });

  final bool enabled;
  final VoidCallback onTap;
  final Widget child;

  @override
  State<_BouncyPrimaryButton> createState() => _BouncyPrimaryButtonState();
}

class _BouncyPrimaryButtonState extends State<_BouncyPrimaryButton> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTapDown: widget.enabled ? (_) => setState(() => _pressed = true) : null,
      onTapCancel:
          widget.enabled ? () => setState(() => _pressed = false) : null,
      onTapUp: widget.enabled
          ? (_) {
              setState(() => _pressed = false);
              widget.onTap();
            }
          : null,
      child: AnimatedScale(
        scale: _pressed ? 0.978 : 1,
        duration: const Duration(milliseconds: 145),
        curve: Curves.easeOutCubic,
        child: AnimatedOpacity(
          opacity: widget.enabled ? 1 : 0.58,
          duration: const Duration(milliseconds: 180),
          child: Container(
            height: 54,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                colors: [Color(0xff9c92fa), Color(0xff7d6ff0)],
              ),
              borderRadius: BorderRadius.circular(28),
              boxShadow: const [
                BoxShadow(
                  color: Color(0x2c7b6ff0),
                  blurRadius: 22,
                  offset: Offset(0, 9),
                ),
              ],
            ),
            child: widget.child,
          ),
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
        GestureDetector(
          onTap: onChanged == null ? null : () => onChanged!(!value),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              AnimatedContainer(
                duration: const Duration(milliseconds: 180),
                width: 23,
                height: 23,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: value ? _violet : Colors.transparent,
                  border: Border.all(
                    color: value ? _violet : const Color(0xffd9d6e8),
                    width: 1.5,
                  ),
                ),
                child: value
                    ? const Icon(
                        Icons.check_rounded,
                        color: Colors.white,
                        size: 16,
                      )
                    : null,
              ),
              const SizedBox(width: 9),
              const Text(
                '记住登录状态',
                style: TextStyle(
                  color: Color(0xff79758e),
                  fontSize: 13.5,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ],
          ),
        ),
        const Spacer(),
        if (onForgot != null)
          TextButton(
            onPressed: onForgot,
            style: TextButton.styleFrom(
              foregroundColor: _violetDeep,
              padding: const EdgeInsets.symmetric(horizontal: 2),
              minimumSize: const Size(0, 0),
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: const Text(
              '忘记密码？',
              style: TextStyle(fontWeight: FontWeight.w600),
            ),
          ),
      ],
    );
  }
}

class _QQButton extends StatefulWidget {
  const _QQButton({required this.onTap});

  final VoidCallback? onTap;

  @override
  State<_QQButton> createState() => _QQButtonState();
}

class _QQButtonState extends State<_QQButton> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTapDown:
          widget.onTap == null ? null : (_) => setState(() => _pressed = true),
      onTapCancel:
          widget.onTap == null ? null : () => setState(() => _pressed = false),
      onTapUp: widget.onTap == null
          ? null
          : (_) {
              setState(() => _pressed = false);
              widget.onTap!();
            },
      child: AnimatedScale(
        scale: _pressed ? 0.94 : 1,
        duration: const Duration(milliseconds: 140),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 54,
              height: 54,
              padding: const EdgeInsets.all(9),
              decoration: BoxDecoration(
                color: const Color(0xfffbfaff),
                shape: BoxShape.circle,
                border: Border.all(color: const Color(0xffe9e6f5)),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x13746bd2),
                    blurRadius: 14,
                    offset: Offset(0, 5),
                  ),
                ],
              ),
              child: Image.asset(
                _qqAsset,
                fit: BoxFit.contain,
                filterQuality: FilterQuality.high,
              ),
            ),
            const SizedBox(height: 7),
            const Text(
              'QQ 登录',
              style: TextStyle(
                color: _violetDeep,
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _EmailLoginButton extends StatefulWidget {
  const _EmailLoginButton({required this.onTap});

  final VoidCallback? onTap;

  @override
  State<_EmailLoginButton> createState() => _EmailLoginButtonState();
}

class _EmailLoginButtonState extends State<_EmailLoginButton> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTapDown:
          widget.onTap == null ? null : (_) => setState(() => _pressed = true),
      onTapCancel:
          widget.onTap == null ? null : () => setState(() => _pressed = false),
      onTapUp: widget.onTap == null
          ? null
          : (_) {
              setState(() => _pressed = false);
              HapticFeedback.selectionClick();
              widget.onTap!();
            },
      child: AnimatedScale(
        scale: _pressed ? 0.94 : 1,
        duration: const Duration(milliseconds: 140),
        curve: Curves.easeOutCubic,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 54,
              height: 54,
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: const Color(0xfffbfaff),
                shape: BoxShape.circle,
                border: Border.all(color: const Color(0xffe9e6f5)),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x13746bd2),
                    blurRadius: 14,
                    offset: Offset(0, 5),
                  ),
                ],
              ),
              child: Image.asset(
                _emailLoginAsset,
                fit: BoxFit.contain,
                filterQuality: FilterQuality.high,
              ),
            ),
            const SizedBox(height: 7),
            const Text(
              '邮箱登录',
              style: TextStyle(
                color: _violetDeep,
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ProviderLoginButton extends StatefulWidget {
  const _ProviderLoginButton({
    required this.asset,
    required this.label,
    required this.onTap,
  });

  final String asset;
  final String label;
  final VoidCallback? onTap;

  @override
  State<_ProviderLoginButton> createState() => _ProviderLoginButtonState();
}

class _ProviderLoginButtonState extends State<_ProviderLoginButton> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final enabled = widget.onTap != null;
    return GestureDetector(
      onTapDown: enabled ? (_) => setState(() => _pressed = true) : null,
      onTapCancel: enabled ? () => setState(() => _pressed = false) : null,
      onTapUp: enabled
          ? (_) {
              setState(() => _pressed = false);
              HapticFeedback.selectionClick();
              widget.onTap!();
            }
          : null,
      child: AnimatedOpacity(
        opacity: enabled ? 1 : 0.38,
        duration: const Duration(milliseconds: 160),
        child: AnimatedScale(
          scale: _pressed ? 0.94 : 1,
          duration: const Duration(milliseconds: 140),
          curve: Curves.easeOutCubic,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 54,
                height: 54,
                padding: const EdgeInsets.all(9),
                decoration: BoxDecoration(
                  color: const Color(0xfffbfaff),
                  shape: BoxShape.circle,
                  border: Border.all(color: const Color(0xffe9e6f5)),
                  boxShadow: const [
                    BoxShadow(
                      color: Color(0x13746bd2),
                      blurRadius: 14,
                      offset: Offset(0, 5),
                    ),
                  ],
                ),
                child: Image.asset(
                  widget.asset,
                  fit: BoxFit.contain,
                  filterQuality: FilterQuality.high,
                ),
              ),
              const SizedBox(height: 7),
              Text(
                widget.label,
                style: const TextStyle(
                  color: _violetDeep,
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
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
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        GestureDetector(
          onTap: onChanged == null ? null : () => onChanged!(!value),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 180),
            width: 21,
            height: 21,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: value ? _violet : Colors.transparent,
              border: Border.all(
                color: value ? _violet : const Color(0xffd9d6e8),
                width: 1.4,
              ),
            ),
            child: value
                ? const Icon(Icons.check_rounded, color: Colors.white, size: 14)
                : null,
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Wrap(
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: 2,
            runSpacing: 1,
            children: [
              const Text(
                '我已阅读并同意',
                style: TextStyle(
                  color: Color(0xff9995aa),
                  fontSize: 11.5,
                ),
              ),
              GestureDetector(
                onTap: onTerms,
                child: const Text(
                  '《用户协议》',
                  style: TextStyle(
                    color: _violetDeep,
                    fontSize: 11.5,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              const Text(
                '和',
                style: TextStyle(color: Color(0xff9995aa), fontSize: 11.5),
              ),
              GestureDetector(
                onTap: onPrivacy,
                child: const Text(
                  '《隐私政策》',
                  style: TextStyle(
                    color: _violetDeep,
                    fontSize: 11.5,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            ],
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
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 9),
      decoration: BoxDecoration(
        color: error ? const Color(0xfffff1f4) : const Color(0xfff7f6fc),
        borderRadius: BorderRadius.circular(15),
        border: Border.all(
          color: error ? const Color(0xffffd4df) : const Color(0xffece9f5),
        ),
      ),
      child: Text(
        text,
        style: TextStyle(
          color: error ? const Color(0xffa74f66) : _muted,
          fontSize: 12.2,
          height: 1.35,
        ),
      ),
    );
  }
}

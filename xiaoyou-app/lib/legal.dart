import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

const xiaoyouPrivacyPolicyUrl = 'https://xiaoyou.yoyoyan.cn/privacy';
const xiaoyouUserAgreementUrl = 'https://xiaoyou.yoyoyan.cn/terms';
const xiaoyouIcpQueryUrl = 'https://beian.miit.gov.cn/';
const xiaoyouAppFilingNumber = '渝ICP备2026017342号-2A';
const xiaoyouWebsiteFilingNumber = '渝ICP备2026017342号-1';

const _privacyConsentVersion = '2026-08-04';
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

class PrivacyConsentGate extends StatefulWidget {
  const PrivacyConsentGate({
    required this.child,
    super.key,
  });

  final Widget child;

  @override
  State<PrivacyConsentGate> createState() => _PrivacyConsentGateState();
}

class _PrivacyConsentGateState extends State<PrivacyConsentGate> {
  bool _loading = true;
  bool _accepted = false;

  @override
  void initState() {
    super.initState();
    _loadConsent();
  }

  Future<void> _loadConsent() async {
    final preferences = await SharedPreferences.getInstance();
    final accepted = preferences.getString(_privacyConsentPreference) ==
        _privacyConsentVersion;
    if (!mounted) {
      return;
    }
    setState(() {
      _accepted = accepted;
      _loading = false;
    });
  }

  Future<void> _accept() async {
    final preferences = await SharedPreferences.getInstance();
    await preferences.setString(
      _privacyConsentPreference,
      _privacyConsentVersion,
    );
    if (mounted) {
      setState(() => _accepted = true);
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

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    if (_accepted) {
      return widget.child;
    }
    return Scaffold(
      backgroundColor: const Color(0xfffff9fc),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 28, 24, 22),
          child: Column(
            children: [
              const Spacer(),
              Container(
                width: 82,
                height: 82,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: const Color(0xffffedf5),
                  border: Border.all(color: Colors.white, width: 3),
                  boxShadow: const [
                    BoxShadow(
                      color: Color(0x249f4f79),
                      blurRadius: 28,
                      offset: Offset(0, 12),
                    ),
                  ],
                ),
                child: const Icon(
                  Icons.favorite_rounded,
                  size: 34,
                  color: Color(0xffb95482),
                ),
              ),
              const SizedBox(height: 24),
              const Text(
                '欢迎来到小悠',
                style: TextStyle(
                  color: Color(0xff30252b),
                  fontSize: 26,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 12),
              const Text(
                '在开始连接前，请先了解我们如何处理聊天、图片、语音、通知和设备同步信息。'
                '只有在你明确同意后，小悠才会连接服务器并启用相关能力。',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: Color(0xff786a71),
                  fontSize: 14,
                  height: 1.65,
                ),
              ),
              const SizedBox(height: 18),
              Wrap(
                alignment: WrapAlignment.center,
                spacing: 6,
                children: [
                  TextButton(
                    onPressed: () => _openDocument(xiaoyouPrivacyPolicyUrl),
                    child: const Text('《隐私政策》'),
                  ),
                  TextButton(
                    onPressed: () => _openDocument(xiaoyouUserAgreementUrl),
                    child: const Text('《用户协议》'),
                  ),
                ],
              ),
              const Spacer(),
              SizedBox(
                width: double.infinity,
                height: 52,
                child: FilledButton(
                  onPressed: _accept,
                  child: const Text('同意并继续'),
                ),
              ),
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                height: 48,
                child: TextButton(
                  onPressed: () => SystemNavigator.pop(),
                  child: const Text('不同意并退出'),
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                '运营者：鄢国栋 · App 备案号：$xiaoyouAppFilingNumber',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: Color(0xffaa9da4),
                  fontSize: 11,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

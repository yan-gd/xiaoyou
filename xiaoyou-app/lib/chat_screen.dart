import 'dart:async';
import 'dart:io';
import 'dart:math';
import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:local_auth/local_auth.dart';

import 'chat_models.dart';
import 'notification_service.dart';
import 'session_store.dart';
import 'voice_recorder.dart';
import 'xiaoyou_api.dart';

const _rose = Color(0xff8f476f);
const _roseDark = Color(0xff572940);
const _ink = Color(0xff30252b);
const _muted = Color(0xff87777f);
const _canvas = Color(0xfffff9fb);
const _avatarAsset = '../assets/xiaoyou-avatar.png';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> with WidgetsBindingObserver {
  final _composer = TextEditingController();
  final _composerFocus = FocusNode();
  final _scrollController = ScrollController();
  final _messages = <ChatMessage>[];
  final _pendingAcknowledgements = <String>{};
  final _receivedActionEvents = <String, Set<String>>{};
  final _renderedActionEvents = <String, Set<String>>{};
  final _expectedActionEvents = <String, int>{};
  final _sessionStore = SessionStore();
  final _localAuth = LocalAuthentication();
  final _voiceRecorder = VoiceRecorderController();
  final _notificationService = AppNotificationService.instance;
  final _imagePicker = ImagePicker();
  final _messageKeys = <String, GlobalKey>{};

  XiaoyouApi? _api;
  SavedConnection? _savedConnection;
  Timer? _pollTimer;
  Timer? _typingTimer;
  Timer? _draftTimer;
  Timer? _highlightTimer;
  Timer? _recordingTimer;
  StreamSubscription<double>? _amplitudeSubscription;
  bool _booting = true;
  bool _connecting = false;
  bool _polling = false;
  bool _sending = false;
  bool _awaitingReply = false;
  bool _voiceMode = false;
  bool _recording = false;
  bool _recordingGestureActive = false;
  bool _recordingCancelling = false;
  bool _accessoryPanelOpen = false;
  bool _emojiPanelOpen = false;
  bool _showJumpToBottom = false;
  bool _locked = false;
  bool _authenticating = false;
  bool _lockEnabled = false;
  bool _appInForeground = true;
  AppPreferences _preferences = const AppPreferences();
  String _status = '尚未连接';
  int _lastEventSequence = 0;
  int _clientSequence = 0;
  int _recordingDurationMs = 0;
  int _newMessageCount = 0;
  double _recordingLevel = 0;
  String _highlightedMessageId = '';
  Future<void>? _recordingStartTask;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _composerFocus.addListener(_handleComposerFocus);
    _composer.addListener(_handleDraftChanged);
    _scrollController.addListener(_handleScroll);
    unawaited(_notificationService.initialize());
    WidgetsBinding.instance.addPostFrameCallback((_) => _restoreSession());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _pollTimer?.cancel();
    _typingTimer?.cancel();
    _draftTimer?.cancel();
    _highlightTimer?.cancel();
    _recordingTimer?.cancel();
    _amplitudeSubscription?.cancel();
    _api?.close();
    unawaited(_voiceRecorder.dispose());
    _composer.dispose();
    _composerFocus
      ..removeListener(_handleComposerFocus)
      ..dispose();
    _scrollController
      ..removeListener(_handleScroll)
      ..dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden) {
      _appInForeground = false;
      if (!_preferences.notificationsEnabled) {
        _pollTimer?.cancel();
      } else {
        _startPolling();
      }
      if (_recording) {
        unawaited(_finishRecording(cancel: true));
      }
      if (_lockEnabled && !_authenticating && mounted) {
        setState(() => _locked = true);
      }
      return;
    }
    if (state != AppLifecycleState.resumed) {
      return;
    }
    _appInForeground = true;
    unawaited(_notificationService.cancelAll());
    if (_lockEnabled && _locked && !_authenticating) {
      unawaited(_unlock());
      return;
    }
    _startPolling();
    unawaited(_poll());
  }

  @override
  void didChangeMetrics() {
    if (!mounted || !_composerFocus.hasFocus) {
      return;
    }
    _scrollToEnd(
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOutCubic,
    );
  }

  void _handleComposerFocus() {
    if (!_composerFocus.hasFocus) {
      return;
    }
    if (_accessoryPanelOpen || _emojiPanelOpen) {
      setState(() {
        _accessoryPanelOpen = false;
        _emojiPanelOpen = false;
      });
    }
    _scrollToEnd();
  }

  void _handleDraftChanged() {
    _draftTimer?.cancel();
    _draftTimer = Timer(
      const Duration(milliseconds: 350),
      () => _sessionStore.saveDraft(_composer.text),
    );
  }

  void _handleScroll() {
    if (!_scrollController.hasClients) {
      return;
    }
    final distance =
        _scrollController.position.maxScrollExtent - _scrollController.offset;
    final show = distance > 180;
    if (show != _showJumpToBottom && mounted) {
      setState(() {
        _showJumpToBottom = show;
        if (!show) {
          _newMessageCount = 0;
        }
      });
    }
  }

  void _pinConversationToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted ||
          !_composerFocus.hasFocus ||
          !_scrollController.hasClients) {
        return;
      }
      _scrollController.jumpTo(_scrollController.position.maxScrollExtent);
    });
  }

  void _closeInputPanels() {
    if (!_accessoryPanelOpen && !_emojiPanelOpen) {
      return;
    }
    setState(() {
      _accessoryPanelOpen = false;
      _emojiPanelOpen = false;
    });
  }

  void _toggleAccessoryPanel() {
    _composerFocus.unfocus();
    setState(() {
      _accessoryPanelOpen = !_accessoryPanelOpen;
      _emojiPanelOpen = false;
    });
    _scrollToEnd();
    HapticFeedback.selectionClick();
  }

  void _toggleEmojiPanel() {
    if (_emojiPanelOpen) {
      setState(() => _emojiPanelOpen = false);
      _composerFocus.requestFocus();
      _scrollToEnd();
      HapticFeedback.selectionClick();
      return;
    }
    _composerFocus.unfocus();
    setState(() {
      _emojiPanelOpen = true;
      _accessoryPanelOpen = false;
    });
    _scrollToEnd();
    HapticFeedback.selectionClick();
  }

  void _insertEmoji(String emoji) {
    final value = _composer.value;
    final selection = value.selection.isValid
        ? value.selection
        : TextSelection.collapsed(offset: value.text.length);
    final start = max(0, selection.start);
    final end = max(start, selection.end);
    final next = value.text.replaceRange(start, end, emoji);
    _composer.value = value.copyWith(
      text: next,
      selection: TextSelection.collapsed(offset: start + emoji.length),
      composing: TextRange.empty,
    );
    HapticFeedback.selectionClick();
  }

  Future<void> _restoreSession() async {
    try {
      final saved = await _sessionStore.loadConnection();
      final draft = await _sessionStore.readDraft();
      final preferences = await _sessionStore.loadPreferences();
      if (!mounted) {
        return;
      }
      setState(() {
        _savedConnection = saved;
        _preferences = preferences;
        _lockEnabled = saved?.appLockEnabled ?? false;
        _locked = saved?.appLockEnabled ?? false;
        _booting = false;
      });
      if (draft.isNotEmpty && _composer.text.isEmpty) {
        _composer
          ..text = draft
          ..selection = TextSelection.collapsed(offset: draft.length);
      }
      if (saved == null) {
        return;
      }
      if (saved.appLockEnabled) {
        await _unlock();
      } else {
        await _connectSaved(saved);
      }
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _booting = false;
        _status = '本机登录信息读取失败';
      });
      _showNotice('无法读取本机登录', '$error');
    }
  }

  Future<void> _connectSaved(SavedConnection saved) async {
    final token = await _sessionStore.readToken();
    if (token == null) {
      await _sessionStore.clear();
      if (!mounted) {
        return;
      }
      setState(() {
        _savedConnection = null;
        _lockEnabled = false;
        _locked = false;
        _status = '需要重新连接';
      });
      return;
    }
    await _connect(
      baseUrl: saved.baseUrl,
      token: token,
      deviceId: saved.deviceId,
      persist: false,
    );
  }

  Future<bool> _authenticate(String reason) async {
    if (_authenticating) {
      return false;
    }
    setState(() => _authenticating = true);
    try {
      if (!await _localAuth.isDeviceSupported()) {
        _showNotice('设备不支持 App 锁', '请先在系统中设置指纹、面容或锁屏密码。');
        return false;
      }
      return await _localAuth.authenticate(
        localizedReason: reason,
        biometricOnly: false,
        persistAcrossBackgrounding: true,
      );
    } on LocalAuthException catch (error) {
      _showNotice('没有完成解锁', _localAuthMessage(error));
      return false;
    } catch (error) {
      _showNotice('没有完成解锁', '$error');
      return false;
    } finally {
      if (mounted) {
        setState(() => _authenticating = false);
      }
    }
  }

  Future<void> _unlock() async {
    final unlocked = await _authenticate('解锁你和小悠的私密会话');
    if (!unlocked || !mounted) {
      return;
    }
    setState(() => _locked = false);
    final saved = _savedConnection;
    if (_api == null && saved != null) {
      await _connectSaved(saved);
    } else {
      _startPolling();
      await _poll();
    }
  }

  Future<void> _connect({
    required String baseUrl,
    required String token,
    required String deviceId,
    required bool persist,
  }) async {
    if (_connecting) {
      return;
    }
    setState(() {
      _connecting = true;
      _status = '正在连接…';
    });
    final api = XiaoyouApi(
      baseUrl: baseUrl,
      token: token,
      deviceId: deviceId,
    );
    final previousApi = _api;
    var activated = false;
    try {
      await api.health();
      await api.registerDevice();
      final history = await api.history();
      if (!mounted) {
        api.close();
        return;
      }
      final connection = SavedConnection(
        baseUrl: baseUrl.replaceFirst(RegExp(r'/+$'), ''),
        deviceId: deviceId,
        appLockEnabled: _lockEnabled,
      );
      if (persist) {
        await _sessionStore.saveConnection(connection, token);
      }
      setState(() {
        _api = api;
        _savedConnection = connection;
        _messages
          ..clear()
          ..addAll(history.messages);
        _lastEventSequence = history.lastEventSequence;
        _status = '在线';
        if (persist) {
          _locked = false;
        }
      });
      activated = true;
      previousApi?.close();
      _registerDeliveryEvents(history.messages);
      await WidgetsBinding.instance.endOfFrame;
      await _flushAcknowledgements();
      _startPolling();
      await _poll();
      _scrollToEnd(animated: false);
      HapticFeedback.selectionClick();
    } catch (error) {
      if (!activated) {
        api.close();
      }
      if (mounted) {
        setState(() => _status = '连接失败');
        _showNotice('连接小悠失败', _friendlyNetworkError(error));
      }
    } finally {
      if (mounted) {
        setState(() => _connecting = false);
      }
    }
  }

  void _startPolling() {
    _pollTimer?.cancel();
    if (_api == null || !_pollingAllowed) {
      return;
    }
    _pollTimer = Timer.periodic(
      Duration(seconds: _appInForeground ? 2 : 15),
      (_) => _poll(),
    );
  }

  Future<void> _poll() async {
    final api = _api;
    if (api == null ||
        !_pollingAllowed ||
        _connecting ||
        _polling ||
        _sending) {
      return;
    }
    _polling = true;
    try {
      final events = await api.eventsAfter(_lastEventSequence);
      if (!mounted) {
        return;
      }
      if (events.isNotEmpty) {
        final known = _messages.map((message) => message.id).toSet();
        final additions = <ChatMessage>[];
        final actions = <String>{};
        var receivedAssistantReply = false;
        for (final event in events) {
          _lastEventSequence = max(
            _lastEventSequence,
            asInt(event['sequence']),
          );
          final message = ChatMessage.fromJson(event);
          if (message.id.isNotEmpty && known.add(message.id)) {
            additions.add(message);
            receivedAssistantReply =
                receivedAssistantReply || message.fromXiaoyou;
          }
          if (message.actionId.isNotEmpty) {
            actions.add(message.actionId);
          }
        }
        if (additions.isNotEmpty) {
          final shouldFollow = !_showJumpToBottom;
          final incomingCount =
              additions.where((message) => message.fromXiaoyou).length;
          _registerDeliveryEvents(additions);
          setState(() {
            _messages.addAll(additions);
            if (receivedAssistantReply) {
              _awaitingReply = false;
            }
            if (!shouldFollow) {
              _newMessageCount += incomingCount;
              _showJumpToBottom = true;
            }
          });
          if (receivedAssistantReply) {
            _typingTimer?.cancel();
          }
          if (!_appInForeground && _preferences.notificationsEnabled) {
            unawaited(_notifyIncoming(additions));
          }
          if (shouldFollow) {
            _scrollToEnd();
          }
        }
        await WidgetsBinding.instance.endOfFrame;
        for (final actionId in actions) {
          _queueAcknowledgementIfRendered(actionId);
        }
      }
      await _flushAcknowledgements();
      if (mounted && _status != '在线') {
        setState(() => _status = '在线');
      }
    } catch (_) {
      if (mounted) {
        setState(() => _status = '正在重连…');
      }
    } finally {
      _polling = false;
    }
  }

  bool get _pollingAllowed =>
      !_locked || (!_appInForeground && _preferences.notificationsEnabled);

  Future<void> _notifyIncoming(List<ChatMessage> messages) async {
    for (final message in messages.where((item) => item.fromXiaoyou)) {
      final body = switch (message.kind) {
        'image' => '小悠发来了一张图片',
        'sticker' => '小悠发来了一个表情包',
        'voice' => message.text.trim().isEmpty
            ? '小悠发来了一条语音'
            : '🎙 ${message.text.trim()}',
        _ => message.text,
      };
      await _notificationService.showMessage(
        messageId: message.id,
        body: _preferences.notificationPreview ? body : '小悠发来了一条新消息',
        sound: _preferences.notificationSound,
        vibration: _preferences.notificationVibration,
      );
    }
  }

  Future<void> _flushAcknowledgements() async {
    final api = _api;
    if (api == null || _pendingAcknowledgements.isEmpty) {
      return;
    }
    for (final actionId in _pendingAcknowledgements.toList()) {
      try {
        await api.acknowledge(actionId);
        _pendingAcknowledgements.remove(actionId);
        _receivedActionEvents.remove(actionId);
        _renderedActionEvents.remove(actionId);
        _expectedActionEvents.remove(actionId);
      } catch (_) {
        // Keep the immutable action id pending for the next successful poll.
      }
    }
  }

  void _registerDeliveryEvents(Iterable<ChatMessage> messages) {
    for (final message in messages) {
      if (!message.fromXiaoyou ||
          message.actionId.isEmpty ||
          message.id.isEmpty ||
          message.terminalStatus != 'queued') {
        continue;
      }
      _receivedActionEvents
          .putIfAbsent(message.actionId, () => <String>{})
          .add(message.id);
      _expectedActionEvents[message.actionId] = max(
        _expectedActionEvents[message.actionId] ?? 0,
        message.requestedParts,
      );
      if (message.kind == 'text') {
        _renderedActionEvents
            .putIfAbsent(message.actionId, () => <String>{})
            .add(message.id);
      }
      _queueAcknowledgementIfRendered(message.actionId);
    }
  }

  void _markEventRendered(ChatMessage message) {
    if (message.actionId.isEmpty ||
        message.id.isEmpty ||
        message.terminalStatus != 'queued') {
      return;
    }
    _renderedActionEvents
        .putIfAbsent(message.actionId, () => <String>{})
        .add(message.id);
    _queueAcknowledgementIfRendered(message.actionId);
    unawaited(_flushAcknowledgements());
  }

  void _queueAcknowledgementIfRendered(String actionId) {
    final expected = _expectedActionEvents[actionId] ?? 0;
    final received = _receivedActionEvents[actionId]?.length ?? 0;
    final rendered = _renderedActionEvents[actionId]?.length ?? 0;
    if (expected > 0 && received >= expected && rendered >= expected) {
      _pendingAcknowledgements.add(actionId);
    }
  }

  Future<void> _send() async {
    final api = _api;
    final text = _composer.text.trim();
    if (api == null) {
      await _openConnectionSheet();
      return;
    }
    if (text.isEmpty || _sending) {
      return;
    }
    final messageId = _newId('msg');
    final createdAt = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    setState(() {
      _sending = true;
      _composer.clear();
      _messages.add(
        ChatMessage(
          id: messageId,
          role: 'user',
          kind: 'text',
          text: text,
          createdAt: createdAt,
          localState: 'sending',
        ),
      );
    });
    HapticFeedback.lightImpact();
    _scrollToEnd();
    try {
      _clientSequence += 1;
      final accepted = await api.sendText(
        messageId: messageId,
        text: text,
        sequence: _clientSequence,
      );
      if (!accepted) {
        throw const HttpException('服务器没有接受这条消息');
      }
      _updateLocalMessage(messageId, 'sent');
      _beginWaitingForReply();
    } catch (error) {
      _updateLocalMessage(messageId, 'failed');
      _showSnack('发送失败，点红色感叹号可重新编辑');
    } finally {
      if (mounted) {
        setState(() => _sending = false);
      }
    }
  }

  void _setVoiceMode(bool enabled) {
    if (_recording) {
      return;
    }
    setState(() => _voiceMode = enabled);
    if (enabled) {
      _composerFocus.unfocus();
      _closeInputPanels();
      unawaited(_voiceRecorder.prepare());
    } else {
      _composerFocus.requestFocus();
      _handleComposerFocus();
    }
    HapticFeedback.selectionClick();
  }

  Future<void> _startRecording() async {
    if (_api == null || _sending || _recording) {
      return;
    }
    _composerFocus.unfocus();
    _recordingGestureActive = true;
    setState(() {
      _recording = true;
      _recordingCancelling = false;
      _recordingDurationMs = 0;
      _recordingLevel = 0;
    });
    HapticFeedback.mediumImpact();
    final task = _beginRecording();
    _recordingStartTask = task;
    await task;
  }

  Future<void> _beginRecording() async {
    try {
      final allowed = await _voiceRecorder.start();
      if (!allowed) {
        _recordingGestureActive = false;
        if (mounted) {
          setState(() => _recording = false);
        }
        _showNotice('需要麦克风权限', '请在系统设置中允许小悠使用麦克风，才能发送语音。');
        return;
      }
      if (!mounted) {
        await _voiceRecorder.cancel();
        return;
      }
      if (!_recordingGestureActive) {
        await _voiceRecorder.cancel();
        if (mounted) {
          setState(() => _recording = false);
        }
        return;
      }
      _amplitudeSubscription?.cancel();
      _amplitudeSubscription =
          _voiceRecorder.amplitudeStream().listen((decibels) {
        if (!mounted || !_recording) {
          return;
        }
        final normalized = ((decibels + 55) / 55).clamp(0.04, 1.0);
        setState(() => _recordingLevel = normalized);
      });
      _recordingTimer?.cancel();
      _recordingTimer = Timer.periodic(
        const Duration(milliseconds: 100),
        (timer) {
          if (!mounted || !_recording) {
            timer.cancel();
            return;
          }
          setState(() => _recordingDurationMs += 100);
          if (_recordingDurationMs >= 60000) {
            unawaited(_finishRecording(cancel: false));
          }
        },
      );
    } catch (error) {
      _recordingGestureActive = false;
      if (mounted) {
        setState(() => _recording = false);
      }
      _showSnack('录音启动失败，请稍后重试');
    }
  }

  void _setRecordingCancelling(bool cancelling) {
    if (!_recording || cancelling == _recordingCancelling) {
      return;
    }
    setState(() => _recordingCancelling = cancelling);
    HapticFeedback.selectionClick();
  }

  Future<void> _finishRecording({required bool cancel}) async {
    if (!_recording && _recordingStartTask == null) {
      return;
    }
    _recordingGestureActive = false;
    final startTask = _recordingStartTask;
    if (startTask != null) {
      await startTask;
      _recordingStartTask = null;
    }
    if (!_recording) {
      return;
    }
    _recordingTimer?.cancel();
    await _amplitudeSubscription?.cancel();
    _amplitudeSubscription = null;
    final shouldCancel = cancel || _recordingCancelling;
    final elapsed = _recordingDurationMs;
    setState(() {
      _recording = false;
      _recordingCancelling = false;
      _recordingLevel = 0;
    });
    if (shouldCancel) {
      await _voiceRecorder.cancel();
      _showSnack('已取消发送');
      return;
    }
    final recorded = await _voiceRecorder.stop();
    if (recorded == null || elapsed < 600) {
      if (recorded != null) {
        final file = File(recorded.path);
        if (await file.exists()) {
          await file.delete();
        }
      }
      _showSnack('说话时间太短啦');
      return;
    }
    await _sendVoice(
      messageId: _newId('voice'),
      path: recorded.path,
      mimeType: recorded.mimeType,
      durationMs: recorded.durationMs,
      addLocalMessage: true,
    );
  }

  Future<void> _sendVoice({
    required String messageId,
    required String path,
    required String mimeType,
    required int durationMs,
    required bool addLocalMessage,
  }) async {
    final api = _api;
    if (api == null || _sending) {
      return;
    }
    final file = File(path);
    if (!await file.exists()) {
      _showSnack('这条录音已经不存在了，请重新录制');
      return;
    }
    final createdAt = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    if (addLocalMessage) {
      setState(() {
        _sending = true;
        _messages.add(
          ChatMessage(
            id: messageId,
            role: 'user',
            kind: 'voice',
            text: '正在识别…',
            mediaId: '',
            mimeType: mimeType,
            durationMs: durationMs,
            localPath: path,
            createdAt: createdAt,
            localState: 'sending',
          ),
        );
      });
    } else {
      setState(() {
        _sending = true;
        final index = _messages.indexWhere(
          (message) => message.id == messageId,
        );
        if (index >= 0) {
          _messages[index] = _messages[index].copyWith(localState: 'sending');
        }
      });
    }
    HapticFeedback.lightImpact();
    _scrollToEnd();
    try {
      _clientSequence += 1;
      final result = await api.sendVoice(
        messageId: messageId,
        audioBytes: await file.readAsBytes(),
        mimeType: mimeType,
        durationMs: durationMs,
        sequence: _clientSequence,
      );
      if (!result.accepted && !result.duplicate) {
        throw const HttpException('服务器没有接受这条语音');
      }
      final index = _messages.indexWhere((message) => message.id == messageId);
      if (mounted && index >= 0) {
        setState(() {
          _messages[index] = _messages[index].copyWith(
            text: result.text.isEmpty ? '语音消息' : result.text,
            mediaId: result.mediaId,
            mimeType: result.mimeType,
            durationMs: result.durationMs > 0 ? result.durationMs : durationMs,
            localPath: '',
            localState: 'sent',
          );
        });
      }
      if (await file.exists()) {
        await file.delete();
      }
      _beginWaitingForReply();
    } catch (error) {
      _updateLocalMessage(messageId, 'failed');
      _showSnack('语音发送失败，点红色感叹号可重试');
    } finally {
      if (mounted) {
        setState(() => _sending = false);
      }
    }
  }

  void _updateLocalMessage(String messageId, String state) {
    if (!mounted) {
      return;
    }
    final index = _messages.indexWhere((message) => message.id == messageId);
    if (index < 0) {
      return;
    }
    setState(() {
      _messages[index] = _messages[index].copyWith(localState: state);
    });
  }

  void _beginWaitingForReply() {
    _typingTimer?.cancel();
    setState(() => _awaitingReply = true);
    _typingTimer = Timer(const Duration(seconds: 90), () {
      if (mounted) {
        setState(() => _awaitingReply = false);
      }
    });
  }

  Future<void> _pickImage({
    required ImageSource source,
    required bool sticker,
  }) async {
    if (_api == null || _sending) {
      return;
    }
    _closeInputPanels();
    try {
      final picked = await _imagePicker.pickImage(
        source: source,
        maxWidth: sticker ? null : 2560,
        maxHeight: sticker ? null : 2560,
        imageQuality: sticker ? null : 88,
        requestFullMetadata: false,
      );
      if (picked == null || !mounted) {
        return;
      }
      final file = File(picked.path);
      final length = await file.length();
      if (length <= 0 || length > 8 * 1024 * 1024) {
        _showNotice('图片无法发送', '请选择一张小于 8 MB 的图片。');
        return;
      }
      final mimeType = picked.mimeType ?? _imageMimeType(picked.path);
      if (!const {
        'image/jpeg',
        'image/png',
        'image/webp',
        'image/gif',
      }.contains(mimeType)) {
        _showNotice('图片格式不支持', '请选择 JPG、PNG、WebP 或 GIF 图片。');
        return;
      }
      await _sendImage(
        messageId: _newId(sticker ? 'sticker' : 'image'),
        path: picked.path,
        mimeType: mimeType,
        kind: sticker ? 'sticker' : 'image',
        addLocalMessage: true,
      );
    } catch (error) {
      _showSnack('暂时无法读取图片，请检查相册权限');
    }
  }

  String _imageMimeType(String path) {
    final lower = path.toLowerCase();
    if (lower.endsWith('.png')) {
      return 'image/png';
    }
    if (lower.endsWith('.webp')) {
      return 'image/webp';
    }
    if (lower.endsWith('.gif')) {
      return 'image/gif';
    }
    return 'image/jpeg';
  }

  Future<void> _sendImage({
    required String messageId,
    required String path,
    required String mimeType,
    required String kind,
    required bool addLocalMessage,
  }) async {
    final api = _api;
    if (api == null || _sending) {
      return;
    }
    final file = File(path);
    if (!await file.exists()) {
      _showSnack('原图片已不存在，请重新选择');
      return;
    }
    final placeholder = kind == 'sticker' ? '[表情包]' : '[图片]';
    if (addLocalMessage) {
      setState(() {
        _sending = true;
        _messages.add(
          ChatMessage(
            id: messageId,
            role: 'user',
            kind: kind,
            text: placeholder,
            mimeType: mimeType,
            localPath: path,
            createdAt: DateTime.now().millisecondsSinceEpoch ~/ 1000,
            localState: 'sending',
          ),
        );
      });
    } else {
      setState(() {
        _sending = true;
        final index = _messages.indexWhere((item) => item.id == messageId);
        if (index >= 0) {
          _messages[index] = _messages[index].copyWith(localState: 'sending');
        }
      });
    }
    HapticFeedback.lightImpact();
    _scrollToEnd();
    try {
      _clientSequence += 1;
      final result = await api.sendImage(
        messageId: messageId,
        imageBytes: await file.readAsBytes(),
        mimeType: mimeType,
        kind: kind,
        sequence: _clientSequence,
      );
      if (!result.accepted && !result.duplicate) {
        throw const HttpException('服务器没有接受这张图片');
      }
      if (mounted) {
        final index = _messages.indexWhere((item) => item.id == messageId);
        if (index >= 0) {
          setState(() {
            _messages[index] = _messages[index].copyWith(
              mediaId: result.mediaId,
              mimeType: result.mimeType,
              localState: 'sent',
            );
          });
        }
      }
      _beginWaitingForReply();
    } catch (error) {
      _updateLocalMessage(messageId, 'failed');
      _showSnack('图片发送失败，点红色感叹号可重试');
    } finally {
      if (mounted) {
        setState(() => _sending = false);
      }
    }
  }

  void _replyToMessage(ChatMessage message) {
    final content = message.text.trim().isEmpty
        ? (message.kind == 'voice' ? '语音消息' : '图片')
        : message.text.trim();
    final brief =
        content.length > 36 ? '${content.substring(0, 36)}…' : content;
    final speaker = message.fromXiaoyou ? '小悠' : '我';
    _composer.text = '回复$speaker「$brief」\n';
    _composer.selection =
        TextSelection.collapsed(offset: _composer.text.length);
    _setVoiceMode(false);
  }

  Future<void> _openSearch() async {
    if (_messages.isEmpty) {
      _showSnack('还没有可搜索的聊天记录');
      return;
    }
    final selected = await showModalBottomSheet<ChatMessage>(
      context: context,
      useSafeArea: true,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => _MessageSearchSheet(messages: _messages),
    );
    if (selected != null && mounted) {
      _jumpToMessage(selected);
    }
  }

  void _jumpToMessage(ChatMessage message) {
    final index = _messages.indexWhere((item) => item.id == message.id);
    if (index < 0 || !_scrollController.hasClients) {
      return;
    }
    final ratio = _messages.length <= 1 ? 0.0 : index / (_messages.length - 1);
    final estimate = _scrollController.position.maxScrollExtent * ratio;
    _scrollController.animateTo(
      estimate,
      duration: const Duration(milliseconds: 380),
      curve: Curves.easeOutCubic,
    );
    setState(() => _highlightedMessageId = message.id);
    _highlightTimer?.cancel();
    _highlightTimer = Timer(const Duration(seconds: 2), () {
      if (mounted && _highlightedMessageId == message.id) {
        setState(() => _highlightedMessageId = '');
      }
    });
    Future<void>.delayed(const Duration(milliseconds: 420), () {
      final targetContext = _messageKeys[message.id]?.currentContext;
      if (targetContext != null && targetContext.mounted) {
        Scrollable.ensureVisible(
          targetContext,
          duration: const Duration(milliseconds: 260),
          alignment: 0.35,
          curve: Curves.easeOutCubic,
        );
      }
    });
  }

  void _retryFailedMessage(ChatMessage message) {
    if (message.kind == 'voice') {
      if (message.localPath.isEmpty) {
        _showSnack('原录音已经清理，请重新录制');
        return;
      }
      unawaited(
        _sendVoice(
          messageId: message.id,
          path: message.localPath,
          mimeType: message.mimeType.isEmpty ? 'audio/mp4' : message.mimeType,
          durationMs: message.durationMs,
          addLocalMessage: false,
        ),
      );
      return;
    }
    if (message.kind == 'image' || message.kind == 'sticker') {
      if (message.localPath.isEmpty) {
        _showSnack('原图片已不存在，请重新选择');
        return;
      }
      unawaited(
        _sendImage(
          messageId: message.id,
          path: message.localPath,
          mimeType: message.mimeType.isEmpty
              ? _imageMimeType(message.localPath)
              : message.mimeType,
          kind: message.kind,
          addLocalMessage: false,
        ),
      );
      return;
    }
    _composer
      ..text = message.text
      ..selection = TextSelection.collapsed(offset: message.text.length);
    _setVoiceMode(false);
    HapticFeedback.selectionClick();
    _showSnack('消息已放回输入框，确认后重新发送');
  }

  Future<void> _openConnectionSheet() async {
    final saved = _savedConnection;
    final draft = await showModalBottomSheet<_ConnectionDraft>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (context) => _ConnectionSheet(saved: saved),
    );
    if (draft == null || !mounted) {
      return;
    }
    var token = draft.token;
    if (token.isEmpty) {
      token = await _sessionStore.readToken() ?? '';
    }
    if (!draft.baseUrl.startsWith('https://') ||
        token.length < 24 ||
        draft.deviceId.isEmpty) {
      _showNotice('连接信息不完整', '请输入 HTTPS 地址、有效连接令牌和设备名称。');
      return;
    }
    await _connect(
      baseUrl: draft.baseUrl,
      token: token,
      deviceId: draft.deviceId,
      persist: true,
    );
  }

  Future<bool> _setAppLock(bool enabled) async {
    if (enabled == _lockEnabled) {
      return true;
    }
    final confirmed = await _authenticate(
      enabled ? '确认开启小悠 App 锁' : '确认关闭小悠 App 锁',
    );
    if (!confirmed || !mounted) {
      return false;
    }
    await _sessionStore.setAppLockEnabled(enabled);
    setState(() {
      _lockEnabled = enabled;
      _savedConnection = _savedConnection?.copyWith(
        appLockEnabled: enabled,
      );
    });
    return true;
  }

  void _updatePreferences(AppPreferences preferences) {
    setState(() => _preferences = preferences);
    unawaited(_sessionStore.savePreferences(preferences));
  }

  Future<bool> _setNotificationsEnabled(bool enabled) async {
    if (enabled) {
      final allowed = await _notificationService.requestPermission();
      if (!allowed) {
        _showNotice(
          '通知权限未开启',
          '请在系统设置中允许小悠发送通知，然后再打开这个开关。',
        );
        return false;
      }
    } else {
      await _notificationService.cancelAll();
    }
    if (!mounted) {
      return false;
    }
    _updatePreferences(
      _preferences.copyWith(notificationsEnabled: enabled),
    );
    return true;
  }

  void _lockNow() {
    if (!_lockEnabled) {
      return;
    }
    _pollTimer?.cancel();
    setState(() => _locked = true);
  }

  Future<void> _forgetConnection() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('忘记本机登录？'),
        content: const Text('只会删除这台手机保存的地址和连接令牌，不会删除聊天记录或服务器数据。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: Colors.redAccent),
            child: const Text('忘记登录'),
          ),
        ],
      ),
    );
    if (confirmed != true) {
      return;
    }
    await _sessionStore.clear();
    _pollTimer?.cancel();
    _typingTimer?.cancel();
    _api?.close();
    if (!mounted) {
      return;
    }
    setState(() {
      _api = null;
      _savedConnection = null;
      _messages.clear();
      _lockEnabled = false;
      _locked = false;
      _awaitingReply = false;
      _status = '尚未连接';
    });
  }

  void _openSettings() {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (sheetContext) => _SettingsSheet(
        connection: _savedConnection,
        status: _status,
        lockEnabled: _lockEnabled,
        preferences: _preferences,
        onLockChanged: _setAppLock,
        onNotificationsChanged: _setNotificationsEnabled,
        onPreferencesChanged: _updatePreferences,
        onEditConnection: () {
          Navigator.pop(sheetContext);
          unawaited(_openConnectionSheet());
        },
        onLockNow: () {
          Navigator.pop(sheetContext);
          _lockNow();
        },
        onForget: () {
          Navigator.pop(sheetContext);
          unawaited(_forgetConnection());
        },
      ),
    );
  }

  void _openProfile() {
    showModalBottomSheet<void>(
      context: context,
      useSafeArea: true,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => const _ProfileSheet(),
    );
  }

  void _scrollToEnd({
    bool animated = true,
    Duration duration = const Duration(milliseconds: 320),
    Curve curve = Curves.easeOutCubic,
  }) {
    if (mounted && (_showJumpToBottom || _newMessageCount > 0)) {
      setState(() {
        _showJumpToBottom = false;
        _newMessageCount = 0;
      });
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) {
        return;
      }
      final offset = _scrollController.position.maxScrollExtent;
      if (animated) {
        _scrollController.animateTo(
          offset,
          duration: duration,
          curve: curve,
        );
      } else {
        _scrollController.jumpTo(offset);
      }
    });
  }

  void _showNotice(String title, String message) {
    if (!mounted) {
      return;
    }
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Text(message),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('知道了'),
          ),
        ],
      ),
    );
  }

  void _showSnack(String message) {
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(message),
          behavior: SnackBarBehavior.floating,
          margin: const EdgeInsets.fromLTRB(20, 0, 20, 18),
        ),
      );
  }

  @override
  Widget build(BuildContext context) {
    late final Widget screen;
    if (_booting) {
      screen = const _StartupScreen();
    } else if (_locked) {
      screen = _LockScreen(
        authenticating: _authenticating,
        onUnlock: _unlock,
        onReconnect: _openConnectionSheet,
      );
    } else if (_savedConnection == null && _api == null) {
      screen = _WelcomeScreen(
        connecting: _connecting,
        onConnect: _openConnectionSheet,
      );
    } else {
      screen = _buildConversation();
    }
    return MediaQuery(
      data: MediaQuery.of(context).copyWith(
        textScaler: TextScaler.linear(_preferences.fontScale),
      ),
      child: screen,
    );
  }

  Widget _buildConversation() {
    final connected = _api != null;
    final palette = _appearancePalette(_preferences.palette);
    final keyboardInset = MediaQuery.viewInsetsOf(context).bottom;
    return Scaffold(
      resizeToAvoidBottomInset: false,
      body: TweenAnimationBuilder<double>(
        tween: Tween<double>(end: keyboardInset),
        duration: const Duration(milliseconds: 230),
        curve: Curves.easeOutCubic,
        builder: (context, bottomInset, child) {
          if (_composerFocus.hasFocus) {
            _pinConversationToBottom();
          }
          return Padding(
            padding: EdgeInsets.only(bottom: bottomInset),
            child: child,
          );
        },
        child: DecoratedBox(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: palette.background,
            ),
          ),
          child: SafeArea(
            child: Column(
              children: [
                _ConversationHeader(
                  status: _status,
                  connected: connected,
                  onAvatarTap: _openProfile,
                  onSearch: _openSearch,
                  onSettings: _openSettings,
                ),
                AnimatedSwitcher(
                  duration: const Duration(milliseconds: 240),
                  child: _status == '在线'
                      ? const SizedBox.shrink(key: ValueKey('online'))
                      : _ConnectionBanner(
                          key: ValueKey(_status),
                          status: _status,
                          onRetry: () {
                            final saved = _savedConnection;
                            if (saved != null) {
                              unawaited(_connectSaved(saved));
                            }
                          },
                        ),
                ),
                Expanded(
                  child: Stack(
                    children: [
                      Positioned.fill(
                        child: _messages.isEmpty
                            ? const _EmptyConversation()
                            : ListView.builder(
                                controller: _scrollController,
                                keyboardDismissBehavior:
                                    ScrollViewKeyboardDismissBehavior.onDrag,
                                padding: EdgeInsets.fromLTRB(
                                  12,
                                  18,
                                  12,
                                  _awaitingReply ? 74 : 20,
                                ),
                                itemCount: _messages.length,
                                itemBuilder: (context, index) {
                                  final message = _messages[index];
                                  final previous =
                                      index > 0 ? _messages[index - 1] : null;
                                  final next = index + 1 < _messages.length
                                      ? _messages[index + 1]
                                      : null;
                                  return _MessageRow(
                                    key: _messageKeys.putIfAbsent(
                                      message.id,
                                      GlobalKey.new,
                                    ),
                                    message: message,
                                    api: _api,
                                    userBubbleColor: palette.userBubble,
                                    bubbleRadius: _preferences.bubbleRadius,
                                    compact: _preferences.compactMessages,
                                    highlighted:
                                        _highlightedMessageId == message.id,
                                    showDate: previous == null ||
                                        !_sameDay(
                                          previous.timestamp,
                                          message.timestamp,
                                        ),
                                    beginsGroup: previous == null ||
                                        previous.fromXiaoyou !=
                                            message.fromXiaoyou ||
                                        message.createdAt - previous.createdAt >
                                            180,
                                    showAvatar: message.fromXiaoyou &&
                                        (next == null ||
                                            !next.fromXiaoyou ||
                                            next.createdAt - message.createdAt >
                                                180),
                                    animate:
                                        index >= max(0, _messages.length - 12),
                                    onRendered: _markEventRendered,
                                    onFailedTap: _retryFailedMessage,
                                    onReply: _replyToMessage,
                                  );
                                },
                              ),
                      ),
                      if (_awaitingReply)
                        const Positioned(
                          left: 12,
                          bottom: 10,
                          child: _TypingIndicator(),
                        ),
                      if (_showJumpToBottom)
                        Positioned(
                          right: 14,
                          bottom: 12,
                          child: _JumpToBottomButton(
                            count: _newMessageCount,
                            onTap: _scrollToEnd,
                          ),
                        ),
                    ],
                  ),
                ),
                _Composer(
                  controller: _composer,
                  focusNode: _composerFocus,
                  sending: _sending,
                  connected: connected,
                  voiceMode: _voiceMode,
                  recording: _recording,
                  recordingCancelling: _recordingCancelling,
                  recordingDurationMs: _recordingDurationMs,
                  recordingLevel: _recordingLevel,
                  onSend: _send,
                  onVoiceModeChanged: _setVoiceMode,
                  onRecordStart: _startRecording,
                  onRecordEnd: (cancel) => _finishRecording(cancel: cancel),
                  onRecordCancelChanged: _setRecordingCancelling,
                  onComposerTap: _handleComposerFocus,
                  emojiPanelOpen: _emojiPanelOpen,
                  accessoryPanelOpen: _accessoryPanelOpen,
                  onEmoji: _toggleEmojiPanel,
                  onAccessory: _toggleAccessoryPanel,
                ),
                AnimatedSize(
                  duration: const Duration(milliseconds: 220),
                  curve: Curves.easeOutCubic,
                  child: _emojiPanelOpen
                      ? _EmojiPanel(onSelected: _insertEmoji)
                      : _accessoryPanelOpen
                          ? _AccessoryPanel(
                              onGallery: () => _pickImage(
                                source: ImageSource.gallery,
                                sticker: false,
                              ),
                              onCamera: () => _pickImage(
                                source: ImageSource.camera,
                                sticker: false,
                              ),
                              onSticker: () => _pickImage(
                                source: ImageSource.gallery,
                                sticker: true,
                              ),
                            )
                          : const SizedBox.shrink(),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _ConnectionDraft {
  const _ConnectionDraft({
    required this.baseUrl,
    required this.token,
    required this.deviceId,
  });

  final String baseUrl;
  final String token;
  final String deviceId;
}

class _ConnectionSheet extends StatefulWidget {
  const _ConnectionSheet({required this.saved});

  final SavedConnection? saved;

  @override
  State<_ConnectionSheet> createState() => _ConnectionSheetState();
}

class _ConnectionSheetState extends State<_ConnectionSheet> {
  late final TextEditingController _baseController;
  late final TextEditingController _tokenController;
  late final TextEditingController _deviceController;
  bool _hideToken = true;

  @override
  void initState() {
    super.initState();
    _baseController = TextEditingController(
      text: widget.saved?.baseUrl ?? 'https://xiaoyou.yoyoyan.cn/xiaoyou-app',
    );
    _tokenController = TextEditingController();
    _deviceController = TextEditingController(
      text: widget.saved?.deviceId ?? 'yoyo-phone',
    );
  }

  @override
  void dispose() {
    _baseController.dispose();
    _tokenController.dispose();
    _deviceController.dispose();
    super.dispose();
  }

  void _submit() {
    Navigator.pop(
      context,
      _ConnectionDraft(
        baseUrl: _baseController.text.trim(),
        token: _tokenController.text.trim(),
        deviceId: _deviceController.text.trim(),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: Material(
        color: _canvas,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(30)),
        clipBehavior: Clip.antiAlias,
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(24, 16, 24, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: const Color(0xffddcfd6),
                  borderRadius: BorderRadius.circular(99),
                ),
              ),
              const SizedBox(height: 22),
              const _Avatar(size: 76),
              const SizedBox(height: 14),
              Text(
                widget.saved == null ? '第一次见面' : '连接设置',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: _ink,
                    ),
              ),
              const SizedBox(height: 6),
              Text(
                widget.saved == null
                    ? '只需连接一次，以后打开就能直接找到小悠'
                    : '令牌留空会继续使用本机安全存储中的令牌',
                textAlign: TextAlign.center,
                style: const TextStyle(color: _muted, height: 1.4),
              ),
              const SizedBox(height: 24),
              TextField(
                controller: _baseController,
                keyboardType: TextInputType.url,
                textInputAction: TextInputAction.next,
                decoration: const InputDecoration(
                  labelText: '服务地址',
                  prefixIcon: Icon(Icons.language_rounded),
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _tokenController,
                obscureText: _hideToken,
                enableSuggestions: false,
                autocorrect: false,
                textInputAction: TextInputAction.next,
                decoration: InputDecoration(
                  labelText: widget.saved == null ? '连接令牌' : '新连接令牌（可留空）',
                  prefixIcon: const Icon(Icons.key_rounded),
                  suffixIcon: IconButton(
                    onPressed: () => setState(() => _hideToken = !_hideToken),
                    icon: Icon(
                      _hideToken
                          ? Icons.visibility_off_rounded
                          : Icons.visibility_rounded,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _deviceController,
                textInputAction: TextInputAction.done,
                onSubmitted: (_) => _submit(),
                decoration: const InputDecoration(
                  labelText: '设备名称',
                  prefixIcon: Icon(Icons.phone_iphone_rounded),
                ),
              ),
              const SizedBox(height: 16),
              const _SecurityNote(),
              const SizedBox(height: 22),
              SizedBox(
                width: double.infinity,
                height: 52,
                child: FilledButton.icon(
                  onPressed: _submit,
                  icon: const Icon(Icons.favorite_rounded, size: 19),
                  label: Text(widget.saved == null ? '连接小悠' : '保存并重新连接'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SecurityNote extends StatelessWidget {
  const _SecurityNote();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: const Color(0xfff4eaf0),
        borderRadius: BorderRadius.circular(16),
      ),
      child: const Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.shield_outlined, color: _rose, size: 20),
          SizedBox(width: 10),
          Expanded(
            child: Text(
              '地址和设备名保存在系统偏好中；连接令牌使用系统安全存储，不会写进聊天记录。',
              style: TextStyle(color: _muted, height: 1.4, fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }
}

class _SettingsSheet extends StatefulWidget {
  const _SettingsSheet({
    required this.connection,
    required this.status,
    required this.lockEnabled,
    required this.preferences,
    required this.onLockChanged,
    required this.onNotificationsChanged,
    required this.onPreferencesChanged,
    required this.onEditConnection,
    required this.onLockNow,
    required this.onForget,
  });

  final SavedConnection? connection;
  final String status;
  final bool lockEnabled;
  final AppPreferences preferences;
  final Future<bool> Function(bool) onLockChanged;
  final Future<bool> Function(bool) onNotificationsChanged;
  final ValueChanged<AppPreferences> onPreferencesChanged;
  final VoidCallback onEditConnection;
  final VoidCallback onLockNow;
  final VoidCallback onForget;

  @override
  State<_SettingsSheet> createState() => _SettingsSheetState();
}

class _SettingsSheetState extends State<_SettingsSheet> {
  late bool _lockEnabled = widget.lockEnabled;
  late AppPreferences _preferences = widget.preferences;
  bool _changingLock = false;
  bool _changingNotifications = false;

  Future<void> _changeLock(bool value) async {
    setState(() => _changingLock = true);
    final changed = await widget.onLockChanged(value);
    if (mounted) {
      setState(() {
        _changingLock = false;
        if (changed) {
          _lockEnabled = value;
        }
      });
    }
  }

  void _updatePreferences(AppPreferences preferences) {
    setState(() => _preferences = preferences);
    widget.onPreferencesChanged(preferences);
  }

  Future<void> _changeNotifications(bool value) async {
    setState(() => _changingNotifications = true);
    final changed = await widget.onNotificationsChanged(value);
    if (!mounted) {
      return;
    }
    setState(() {
      _changingNotifications = false;
      if (changed) {
        _preferences = _preferences.copyWith(notificationsEnabled: value);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final connection = widget.connection;
    return Material(
      color: _canvas,
      borderRadius: const BorderRadius.vertical(top: Radius.circular(30)),
      clipBehavior: Clip.antiAlias,
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 14, 20, 30),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: const Color(0xffddcfd6),
                borderRadius: BorderRadius.circular(99),
              ),
            ),
            const SizedBox(height: 22),
            const _Avatar(size: 70),
            const SizedBox(height: 12),
            const Text(
              '小悠',
              style: TextStyle(
                color: _ink,
                fontWeight: FontWeight.w700,
                fontSize: 22,
              ),
            ),
            const SizedBox(height: 4),
            Text(widget.status, style: const TextStyle(color: _muted)),
            const SizedBox(height: 24),
            _SettingsCard(
              children: [
                ListTile(
                  leading: const _SettingsIcon(
                    icon: Icons.lock_outline_rounded,
                  ),
                  title: const Text('App 锁'),
                  subtitle: const Text('打开时使用指纹、面容或锁屏密码'),
                  trailing: _changingLock
                      ? const SizedBox(
                          width: 22,
                          height: 22,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : Switch.adaptive(
                          value: _lockEnabled,
                          onChanged: _changeLock,
                        ),
                ),
                if (_lockEnabled)
                  ListTile(
                    leading: const _SettingsIcon(
                      icon: Icons.fingerprint_rounded,
                    ),
                    title: const Text('立即锁定'),
                    trailing: const Icon(Icons.chevron_right_rounded),
                    onTap: widget.onLockNow,
                  ),
              ],
            ),
            const SizedBox(height: 14),
            _SettingsCard(
              children: [
                ListTile(
                  leading: const _SettingsIcon(
                    icon: Icons.notifications_active_outlined,
                  ),
                  title: const Text('系统通知'),
                  subtitle: const Text('App 在后台保持运行时提醒小悠的新消息'),
                  trailing: _changingNotifications
                      ? const SizedBox(
                          width: 22,
                          height: 22,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : Switch.adaptive(
                          value: _preferences.notificationsEnabled,
                          onChanged: _changeNotifications,
                        ),
                ),
                ListTile(
                  enabled: _preferences.notificationsEnabled,
                  leading: const _SettingsIcon(
                    icon: Icons.volume_up_outlined,
                  ),
                  title: const Text('通知声音'),
                  trailing: Switch.adaptive(
                    value: _preferences.notificationSound,
                    onChanged: _preferences.notificationsEnabled
                        ? (value) => _updatePreferences(
                              _preferences.copyWith(
                                notificationSound: value,
                              ),
                            )
                        : null,
                  ),
                ),
                ListTile(
                  enabled: _preferences.notificationsEnabled,
                  leading: const _SettingsIcon(
                    icon: Icons.visibility_outlined,
                  ),
                  title: const Text('显示消息内容'),
                  subtitle: const Text('关闭后，锁屏通知不展示聊天正文'),
                  trailing: Switch.adaptive(
                    value: _preferences.notificationPreview,
                    onChanged: _preferences.notificationsEnabled
                        ? (value) => _updatePreferences(
                              _preferences.copyWith(
                                notificationPreview: value,
                              ),
                            )
                        : null,
                  ),
                ),
                ListTile(
                  enabled: _preferences.notificationsEnabled,
                  leading: const _SettingsIcon(
                    icon: Icons.vibration_rounded,
                  ),
                  title: const Text('通知振动'),
                  trailing: Switch.adaptive(
                    value: _preferences.notificationVibration,
                    onChanged: _preferences.notificationsEnabled
                        ? (value) => _updatePreferences(
                              _preferences.copyWith(
                                notificationVibration: value,
                              ),
                            )
                        : null,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),
            _SettingsCard(
              children: [
                const ListTile(
                  leading: _SettingsIcon(
                    icon: Icons.palette_outlined,
                  ),
                  title: Text('界面 DIY'),
                  subtitle: Text('更改只保存在这台手机，不影响小悠的人格与记忆'),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        '聊天配色',
                        style: TextStyle(
                          color: _muted,
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Wrap(
                        spacing: 8,
                        children: [
                          for (final item in const [
                            ('rose', '樱粉', Color(0xffa85e85)),
                            ('lilac', '雾紫', Color(0xff826aa8)),
                            ('peach', '奶杏', Color(0xffb77661)),
                          ])
                            ChoiceChip(
                              avatar: CircleAvatar(
                                radius: 7,
                                backgroundColor: item.$3,
                              ),
                              label: Text(item.$2),
                              selected: _preferences.palette == item.$1,
                              onSelected: (_) => _updatePreferences(
                                _preferences.copyWith(palette: item.$1),
                              ),
                              showCheckmark: false,
                            ),
                        ],
                      ),
                      const SizedBox(height: 15),
                      Text(
                        '字体大小 ${(_preferences.fontScale * 100).round()}%',
                        style: const TextStyle(
                          color: _muted,
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      Slider(
                        value: _preferences.fontScale,
                        min: 0.9,
                        max: 1.2,
                        divisions: 6,
                        label: '${(_preferences.fontScale * 100).round()}%',
                        onChanged: (value) => _updatePreferences(
                          _preferences.copyWith(fontScale: value),
                        ),
                      ),
                      Text(
                        '气泡圆角 ${_preferences.bubbleRadius.round()}',
                        style: const TextStyle(
                          color: _muted,
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      Slider(
                        value: _preferences.bubbleRadius,
                        min: 10,
                        max: 24,
                        divisions: 7,
                        onChanged: (value) => _updatePreferences(
                          _preferences.copyWith(bubbleRadius: value),
                        ),
                      ),
                    ],
                  ),
                ),
                SwitchListTile.adaptive(
                  value: _preferences.compactMessages,
                  title: const Text('紧凑消息间距'),
                  subtitle: const Text('同一屏显示更多聊天内容'),
                  secondary: const Icon(
                    Icons.density_small_rounded,
                    color: _rose,
                  ),
                  onChanged: (value) => _updatePreferences(
                    _preferences.copyWith(compactMessages: value),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),
            _SettingsCard(
              children: [
                ListTile(
                  leading: const _SettingsIcon(
                    icon: Icons.cloud_outlined,
                  ),
                  title: const Text('连接设置'),
                  subtitle: Text(
                    connection == null
                        ? '尚未保存'
                        : '${connection.baseUrl}\n${connection.deviceId}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  trailing: const Icon(Icons.chevron_right_rounded),
                  onTap: widget.onEditConnection,
                ),
              ],
            ),
            const SizedBox(height: 14),
            _SettingsCard(
              children: [
                ListTile(
                  leading: const _SettingsIcon(
                    icon: Icons.logout_rounded,
                    danger: true,
                  ),
                  title: const Text(
                    '忘记本机登录',
                    style: TextStyle(color: Colors.redAccent),
                  ),
                  subtitle: const Text('不会删除服务器聊天记录'),
                  onTap: widget.onForget,
                ),
              ],
            ),
            const SizedBox(height: 18),
            const Text(
              '小悠 App · 私人单联系人会话',
              style: TextStyle(color: Color(0xffaa9da4), fontSize: 12),
            ),
          ],
        ),
      ),
    );
  }
}

class _SettingsCard extends StatelessWidget {
  const _SettingsCard({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(22),
      clipBehavior: Clip.antiAlias,
      child: Column(children: children),
    );
  }
}

class _SettingsIcon extends StatelessWidget {
  const _SettingsIcon({required this.icon, this.danger = false});

  final IconData icon;
  final bool danger;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 38,
      height: 38,
      decoration: BoxDecoration(
        color: danger ? const Color(0xffffeeee) : const Color(0xfff4eaf0),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Icon(
        icon,
        color: danger ? Colors.redAccent : _rose,
        size: 21,
      ),
    );
  }
}

class _ConversationHeader extends StatelessWidget {
  const _ConversationHeader({
    required this.status,
    required this.connected,
    required this.onAvatarTap,
    required this.onSearch,
    required this.onSettings,
  });

  final String status;
  final bool connected;
  final VoidCallback onAvatarTap;
  final VoidCallback onSearch;
  final VoidCallback onSettings;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 10, 10, 11),
      decoration: const BoxDecoration(
        color: Color(0xf7fffafd),
        border: Border(bottom: BorderSide(color: Color(0xffeee3e8))),
      ),
      child: Row(
        children: [
          GestureDetector(
            onTap: onAvatarTap,
            child: Stack(
              clipBehavior: Clip.none,
              children: [
                const Hero(
                  tag: 'xiaoyou-avatar',
                  child: _Avatar(size: 47),
                ),
                Positioned(
                  right: -1,
                  bottom: 1,
                  child: _PresenceDot(online: connected),
                ),
              ],
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: GestureDetector(
              onTap: onAvatarTap,
              behavior: HitTestBehavior.opaque,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Row(
                    children: [
                      Text(
                        '小悠',
                        style: TextStyle(
                          color: _ink,
                          fontWeight: FontWeight.w700,
                          fontSize: 18,
                        ),
                      ),
                      SizedBox(width: 6),
                      Icon(
                        Icons.favorite_rounded,
                        size: 13,
                        color: Color(0xffc36d98),
                      ),
                    ],
                  ),
                  const SizedBox(height: 2),
                  AnimatedSwitcher(
                    duration: const Duration(milliseconds: 180),
                    child: Text(
                      status,
                      key: ValueKey(status),
                      style: TextStyle(
                        color: connected ? const Color(0xff5e8d77) : _muted,
                        fontSize: 12,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          IconButton(
            onPressed: onSearch,
            tooltip: '搜索聊天记录',
            icon: const Icon(Icons.search_rounded, color: _ink),
          ),
          IconButton(
            onPressed: onSettings,
            tooltip: '小悠与设置',
            icon: const Icon(Icons.more_horiz_rounded, color: _ink),
          ),
        ],
      ),
    );
  }
}

class _PresenceDot extends StatefulWidget {
  const _PresenceDot({required this.online});

  final bool online;

  @override
  State<_PresenceDot> createState() => _PresenceDotState();
}

class _PresenceDotState extends State<_PresenceDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1500),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color =
        widget.online ? const Color(0xff58a47d) : const Color(0xffb4a8ae);
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) => Container(
        width: 13,
        height: 13,
        decoration: BoxDecoration(
          color: color,
          shape: BoxShape.circle,
          border: Border.all(color: Colors.white, width: 2),
          boxShadow: widget.online
              ? [
                  BoxShadow(
                    color: color.withValues(
                      alpha: 0.16 + _controller.value * 0.18,
                    ),
                    blurRadius: 4 + _controller.value * 4,
                    spreadRadius: _controller.value * 2,
                  ),
                ]
              : null,
        ),
      ),
    );
  }
}

class _ConnectionBanner extends StatelessWidget {
  const _ConnectionBanner({
    super.key,
    required this.status,
    required this.onRetry,
  });

  final String status;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final failed = status == '连接失败';
    return Material(
      color: failed ? const Color(0xffffeeee) : const Color(0xfffff4e1),
      child: InkWell(
        onTap: failed ? onRetry : null,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              if (!failed)
                const SizedBox(
                  width: 13,
                  height: 13,
                  child: CircularProgressIndicator(strokeWidth: 1.8),
                )
              else
                const Icon(
                  Icons.refresh_rounded,
                  size: 17,
                  color: Colors.redAccent,
                ),
              const SizedBox(width: 8),
              Text(
                failed ? '连接失败，轻触重试' : status,
                style: TextStyle(
                  color: failed ? Colors.redAccent : const Color(0xff8e6e3c),
                  fontSize: 12,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _MessageRow extends StatefulWidget {
  const _MessageRow({
    super.key,
    required this.message,
    required this.api,
    required this.userBubbleColor,
    required this.bubbleRadius,
    required this.compact,
    required this.highlighted,
    required this.showDate,
    required this.beginsGroup,
    required this.showAvatar,
    required this.animate,
    required this.onRendered,
    required this.onFailedTap,
    required this.onReply,
  });

  final ChatMessage message;
  final XiaoyouApi? api;
  final Color userBubbleColor;
  final double bubbleRadius;
  final bool compact;
  final bool highlighted;
  final bool showDate;
  final bool beginsGroup;
  final bool showAvatar;
  final bool animate;
  final ValueChanged<ChatMessage> onRendered;
  final ValueChanged<ChatMessage> onFailedTap;
  final ValueChanged<ChatMessage> onReply;

  @override
  State<_MessageRow> createState() => _MessageRowState();
}

class _MessageRowState extends State<_MessageRow>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 300),
    value: widget.animate ? 0 : 1,
  );
  bool _renderReported = false;
  AudioPlayer? _voicePlayer;
  StreamSubscription<PlayerState>? _voiceStateSubscription;
  StreamSubscription<Duration>? _voiceDurationSubscription;
  StreamSubscription<void>? _voiceCompleteSubscription;
  bool _voiceLoading = false;
  bool _voicePlaying = false;
  Duration _voiceDuration = Duration.zero;

  @override
  void initState() {
    super.initState();
    if (widget.animate) {
      _controller.forward();
    }
  }

  @override
  void dispose() {
    _voiceStateSubscription?.cancel();
    _voiceDurationSubscription?.cancel();
    _voiceCompleteSubscription?.cancel();
    _voicePlayer?.dispose();
    _controller.dispose();
    super.dispose();
  }

  void _reportRendered() {
    if (_renderReported) {
      return;
    }
    _renderReported = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        widget.onRendered(widget.message);
      }
    });
  }

  void _copyMessage() {
    final text = widget.message.text.trim();
    if (text.isEmpty) {
      return;
    }
    Clipboard.setData(ClipboardData(text: text));
    HapticFeedback.selectionClick();
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        const SnackBar(
          content: Text('已复制消息'),
          behavior: SnackBarBehavior.floating,
          duration: Duration(seconds: 1),
        ),
      );
  }

  void _openMessageActions() {
    HapticFeedback.mediumImpact();
    showModalBottomSheet<void>(
      context: context,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (sheetContext) => Container(
        margin: const EdgeInsets.all(12),
        padding: const EdgeInsets.symmetric(vertical: 8),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(24),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.reply_rounded, color: _rose),
              title: const Text('回复这条消息'),
              onTap: () {
                Navigator.pop(sheetContext);
                widget.onReply(widget.message);
              },
            ),
            if (widget.message.text.trim().isNotEmpty)
              ListTile(
                leading: const Icon(Icons.copy_rounded, color: _rose),
                title: const Text('复制文字'),
                onTap: () {
                  Navigator.pop(sheetContext);
                  _copyMessage();
                },
              ),
          ],
        ),
      ),
    );
  }

  void _openImage(
    String url,
    Map<String, String>? headers,
    String localPath,
  ) {
    Navigator.of(context).push(
      PageRouteBuilder<void>(
        opaque: false,
        barrierColor: Colors.black,
        transitionDuration: const Duration(milliseconds: 260),
        pageBuilder: (_, __, ___) => _ImageViewer(
          heroTag: 'image-${widget.message.id}',
          url: url,
          headers: headers,
          localPath: localPath,
        ),
      ),
    );
  }

  AudioPlayer _player() {
    final existing = _voicePlayer;
    if (existing != null) {
      return existing;
    }
    final player = AudioPlayer();
    _voiceStateSubscription = player.onPlayerStateChanged.listen((state) {
      if (mounted) {
        setState(() => _voicePlaying = state == PlayerState.playing);
      }
    });
    _voiceDurationSubscription = player.onDurationChanged.listen((duration) {
      if (mounted) {
        setState(() => _voiceDuration = duration);
      }
    });
    _voiceCompleteSubscription = player.onPlayerComplete.listen((_) {
      if (mounted) {
        setState(() => _voicePlaying = false);
      }
    });
    _voicePlayer = player;
    return player;
  }

  Future<void> _toggleVoice() async {
    if (_voiceLoading) {
      return;
    }
    final player = _player();
    if (_voicePlaying) {
      await player.pause();
      return;
    }
    if (player.state == PlayerState.paused) {
      await player.resume();
      return;
    }
    final message = widget.message;
    if (message.localPath.isEmpty &&
        (message.mediaId.isEmpty || widget.api == null)) {
      return;
    }
    setState(() => _voiceLoading = true);
    try {
      if (message.localPath.isNotEmpty) {
        await player.play(
          DeviceFileSource(
            message.localPath,
            mimeType: message.mimeType.isEmpty ? 'audio/mp4' : message.mimeType,
          ),
        );
      } else {
        final media = await widget.api!.downloadMedia(message.mediaId);
        await player.play(
          BytesSource(media.bytes, mimeType: media.mimeType),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(
            const SnackBar(
              content: Text('语音暂时播放失败'),
              behavior: SnackBarBehavior.floating,
            ),
          );
      }
    } finally {
      if (mounted) {
        setState(() => _voiceLoading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final message = widget.message;
    final fromXiaoyou = message.fromXiaoyou;
    final screenWidth = MediaQuery.sizeOf(context).width;
    final maxBubbleWidth = min(screenWidth * 0.76, 340.0);
    final slideBegin = Offset(fromXiaoyou ? -0.06 : 0.06, 0.04);
    return FadeTransition(
      opacity: CurvedAnimation(
        parent: _controller,
        curve: Curves.easeOut,
      ),
      child: SlideTransition(
        position: Tween<Offset>(
          begin: slideBegin,
          end: Offset.zero,
        ).animate(
          CurvedAnimation(parent: _controller, curve: Curves.easeOutCubic),
        ),
        child: Column(
          children: [
            if (widget.showDate) _DateDivider(date: message.timestamp),
            SizedBox(
              height: widget.beginsGroup
                  ? (widget.compact ? 4 : 7)
                  : (widget.compact ? 0 : 2),
            ),
            Row(
              mainAxisAlignment:
                  fromXiaoyou ? MainAxisAlignment.start : MainAxisAlignment.end,
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                if (fromXiaoyou) ...[
                  SizedBox(
                    width: 34,
                    child: widget.showAvatar
                        ? const _Avatar(size: 28)
                        : const SizedBox.shrink(),
                  ),
                  const SizedBox(width: 6),
                ],
                Flexible(
                  child: GestureDetector(
                    onLongPress: _openMessageActions,
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 220),
                      constraints: BoxConstraints(maxWidth: maxBubbleWidth),
                      padding:
                          message.kind == 'image' || message.kind == 'sticker'
                              ? const EdgeInsets.all(4)
                              : EdgeInsets.fromLTRB(
                                  14,
                                  widget.compact ? 7 : 10,
                                  14,
                                  widget.compact ? 7 : 9,
                                ),
                      decoration: BoxDecoration(
                        color: widget.highlighted
                            ? const Color(0xffffe0ee)
                            : fromXiaoyou
                                ? Colors.white
                                : widget.userBubbleColor,
                        border: widget.highlighted
                            ? Border.all(color: _rose, width: 1.5)
                            : null,
                        borderRadius: BorderRadius.only(
                          topLeft: Radius.circular(widget.bubbleRadius),
                          topRight: Radius.circular(widget.bubbleRadius),
                          bottomLeft: Radius.circular(
                            fromXiaoyou && widget.showAvatar
                                ? 6
                                : widget.bubbleRadius,
                          ),
                          bottomRight: Radius.circular(
                            !fromXiaoyou ? 6 : widget.bubbleRadius,
                          ),
                        ),
                        boxShadow: const [
                          BoxShadow(
                            color: Color(0x104c2839),
                            blurRadius: 14,
                            offset: Offset(0, 4),
                          ),
                        ],
                      ),
                      child: message.kind == 'image' ||
                              message.kind == 'sticker'
                          ? _buildImage(message)
                          : message.kind == 'voice'
                              ? _buildVoice(message)
                              : Text(
                                  message.text,
                                  style: TextStyle(
                                    color: fromXiaoyou ? _ink : Colors.white,
                                    fontSize: 16,
                                    height: 1.42,
                                  ),
                                ),
                    ),
                  ),
                ),
                if (!fromXiaoyou) ...[
                  const SizedBox(width: 6),
                  _DeliveryState(
                    state: message.localState,
                    onFailedTap: () => widget.onFailedTap(message),
                  ),
                ],
              ],
            ),
            Padding(
              padding: EdgeInsets.only(
                top: 4,
                left: fromXiaoyou ? 40 : 0,
                right: fromXiaoyou ? 0 : 28,
              ),
              child: Align(
                alignment:
                    fromXiaoyou ? Alignment.centerLeft : Alignment.centerRight,
                child: Text(
                  _formatTime(message.timestamp),
                  style: const TextStyle(
                    color: Color(0xffaaa0a5),
                    fontSize: 10,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildImage(ChatMessage message) {
    final api = widget.api;
    final url = message.mediaId.isNotEmpty && api != null
        ? api.mediaUrl(message.mediaId)
        : message.remoteUrl;
    final headers = message.mediaId.isNotEmpty ? api?.mediaHeaders : null;
    final localPath = message.localPath;
    final hasLocal = localPath.isNotEmpty && File(localPath).existsSync();
    final image = hasLocal
        ? Image.file(
            File(localPath),
            width: message.kind == 'sticker' ? 180 : 258,
            fit: BoxFit.cover,
          )
        : Image.network(
            url,
            headers: headers,
            width: message.kind == 'sticker' ? 180 : 258,
            fit: BoxFit.cover,
          );
    return GestureDetector(
      onTap: !hasLocal && url.isEmpty
          ? null
          : () => _openImage(url, headers, hasLocal ? localPath : ''),
      child: Hero(
        tag: 'image-${message.id}',
        child: ClipRRect(
          borderRadius: BorderRadius.circular(15),
          child: Image(
            image: image.image,
            width: message.kind == 'sticker' ? 180 : 258,
            fit: BoxFit.cover,
            frameBuilder: (context, child, frame, synchronous) {
              if (synchronous || frame != null) {
                _reportRendered();
              }
              return child;
            },
            loadingBuilder: (context, child, progress) {
              if (progress == null) {
                return child;
              }
              return const SizedBox(
                width: 250,
                height: 180,
                child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
              );
            },
            errorBuilder: (_, __, ___) => const SizedBox(
              width: 250,
              height: 150,
              child: Center(child: Text('图片暂时加载失败')),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildVoice(ChatMessage message) {
    _reportRendered();
    final fromXiaoyou = message.fromXiaoyou;
    final reportedMilliseconds = _voiceDuration.inMilliseconds > 0
        ? _voiceDuration.inMilliseconds
        : message.durationMs;
    final milliseconds = reportedMilliseconds > 0 &&
            reportedMilliseconds <= const Duration(minutes: 10).inMilliseconds
        ? reportedMilliseconds
        : 0;
    final seconds =
        milliseconds > 0 ? max(1, (milliseconds / 1000).round()) : 0;
    final canPlay = message.localPath.isNotEmpty ||
        (message.mediaId.isNotEmpty && widget.api != null);
    final bubbleWidth = (150.0 + min(seconds, 30) * 3.2).clamp(150.0, 246.0);
    return InkWell(
      onTap: canPlay ? _toggleVoice : null,
      borderRadius: BorderRadius.circular(16),
      child: SizedBox(
        width: bubbleWidth,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                AnimatedSwitcher(
                  duration: const Duration(milliseconds: 160),
                  child: _voiceLoading
                      ? SizedBox(
                          key: const ValueKey('loading'),
                          width: 22,
                          height: 22,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: fromXiaoyou ? _rose : Colors.white,
                          ),
                        )
                      : Icon(
                          _voicePlaying
                              ? Icons.pause_rounded
                              : Icons.play_arrow_rounded,
                          key: ValueKey(_voicePlaying),
                          color: fromXiaoyou ? _rose : Colors.white,
                          size: 25,
                        ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: SizedBox(
                    height: 25,
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: List.generate(15, (index) {
                        final seed =
                            (message.id.hashCode.abs() + index * 37) % 13;
                        final height = 5.0 + seed * 1.25;
                        return AnimatedContainer(
                          duration: const Duration(milliseconds: 220),
                          width: 2.5,
                          height: _voicePlaying
                              ? max(7, height * (0.75 + (index % 3) * 0.12))
                              : height,
                          decoration: BoxDecoration(
                            color: (fromXiaoyou ? _rose : Colors.white)
                                .withValues(alpha: 0.82),
                            borderRadius: BorderRadius.circular(99),
                          ),
                        );
                      }),
                    ),
                  ),
                ),
                const SizedBox(width: 9),
                Text(
                  seconds > 0 ? '$seconds″' : '语音',
                  style: TextStyle(
                    color: (fromXiaoyou ? _ink : Colors.white)
                        .withValues(alpha: 0.72),
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
            if (message.text.trim().isNotEmpty) ...[
              const SizedBox(height: 7),
              Text(
                message.text,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: (fromXiaoyou ? _ink : Colors.white)
                      .withValues(alpha: 0.74),
                  fontSize: 12,
                  height: 1.35,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _DeliveryState extends StatelessWidget {
  const _DeliveryState({required this.state, required this.onFailedTap});

  final String state;
  final VoidCallback onFailedTap;

  @override
  Widget build(BuildContext context) {
    if (state == 'failed') {
      return GestureDetector(
        onTap: onFailedTap,
        child: const Padding(
          padding: EdgeInsets.only(bottom: 12),
          child: Icon(
            Icons.error_rounded,
            color: Colors.redAccent,
            size: 18,
          ),
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: AnimatedSwitcher(
        duration: const Duration(milliseconds: 180),
        child: state == 'sending'
            ? const SizedBox(
                key: ValueKey('sending'),
                width: 13,
                height: 13,
                child: CircularProgressIndicator(
                  strokeWidth: 1.5,
                  color: Color(0xffaa9da4),
                ),
              )
            : const Icon(
                Icons.done_rounded,
                key: ValueKey('sent'),
                color: Color(0xff9b8f95),
                size: 16,
              ),
      ),
    );
  }
}

class _DateDivider extends StatelessWidget {
  const _DateDivider({required this.date});

  final DateTime date;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 16),
      child: Center(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
          decoration: BoxDecoration(
            color: const Color(0x99eee5e9),
            borderRadius: BorderRadius.circular(99),
          ),
          child: Text(
            _formatDate(date),
            style: const TextStyle(color: _muted, fontSize: 11),
          ),
        ),
      ),
    );
  }
}

class _TypingIndicator extends StatefulWidget {
  const _TypingIndicator();

  @override
  State<_TypingIndicator> createState() => _TypingIndicatorState();
}

class _TypingIndicatorState extends State<_TypingIndicator>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        const _Avatar(size: 28),
        const SizedBox(width: 6),
        Container(
          height: 42,
          padding: const EdgeInsets.symmetric(horizontal: 15),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: const BorderRadius.only(
              topLeft: Radius.circular(18),
              topRight: Radius.circular(18),
              bottomLeft: Radius.circular(6),
              bottomRight: Radius.circular(18),
            ),
            boxShadow: const [
              BoxShadow(
                color: Color(0x104c2839),
                blurRadius: 12,
                offset: Offset(0, 3),
              ),
            ],
          ),
          child: AnimatedBuilder(
            animation: _controller,
            builder: (context, _) => Row(
              children: List.generate(3, (index) {
                final phase = (_controller.value - index * 0.18) % 1.0;
                final lift = sin(phase * pi * 2).clamp(0.0, 1.0) * 3;
                return Transform.translate(
                  offset: Offset(0, -lift),
                  child: Container(
                    width: 6,
                    height: 6,
                    margin: const EdgeInsets.symmetric(horizontal: 2),
                    decoration: const BoxDecoration(
                      color: Color(0xffbc91a8),
                      shape: BoxShape.circle,
                    ),
                  ),
                );
              }),
            ),
          ),
        ),
      ],
    );
  }
}

class _JumpToBottomButton extends StatelessWidget {
  const _JumpToBottomButton({required this.count, required this.onTap});

  final int count;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white,
      elevation: 5,
      shadowColor: const Color(0x33572a40),
      shape: const StadiumBorder(),
      child: InkWell(
        onTap: onTap,
        customBorder: const StadiumBorder(),
        child: Padding(
          padding: EdgeInsets.symmetric(
            horizontal: count > 0 ? 13 : 11,
            vertical: 9,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.keyboard_arrow_down_rounded,
                  color: _rose, size: 22),
              if (count > 0) ...[
                const SizedBox(width: 4),
                Text(
                  '$count 条新消息',
                  style: const TextStyle(
                    color: _roseDark,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _AccessoryPanel extends StatelessWidget {
  const _AccessoryPanel({
    required this.onGallery,
    required this.onCamera,
    required this.onSticker,
  });

  final VoidCallback onGallery;
  final VoidCallback onCamera;
  final VoidCallback onSticker;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 178,
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(22, 20, 22, 26),
      decoration: const BoxDecoration(
        color: Color(0xfffffafd),
        border: Border(top: BorderSide(color: Color(0xffeee3e8))),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _AccessoryItem(
            icon: Icons.photo_library_outlined,
            label: '相册',
            onTap: onGallery,
          ),
          const SizedBox(width: 24),
          _AccessoryItem(
            icon: Icons.camera_alt_outlined,
            label: '拍摄',
            onTap: onCamera,
          ),
          const SizedBox(width: 24),
          _AccessoryItem(
            icon: Icons.gif_box_outlined,
            label: '表情包',
            onTap: onSticker,
          ),
        ],
      ),
    );
  }
}

class _AccessoryItem extends StatelessWidget {
  const _AccessoryItem({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(20),
      child: SizedBox(
        width: 72,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 62,
              height: 62,
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: const Color(0xffeadde4)),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x10572a40),
                    blurRadius: 12,
                    offset: Offset(0, 4),
                  ),
                ],
              ),
              child: Icon(icon, color: _rose, size: 28),
            ),
            const SizedBox(height: 8),
            Text(label, style: const TextStyle(color: _muted, fontSize: 12)),
          ],
        ),
      ),
    );
  }
}

class _EmojiPanel extends StatelessWidget {
  const _EmojiPanel({required this.onSelected});

  static const _emojis = [
    '😊',
    '🥰',
    '🥹',
    '😘',
    '🤗',
    '😋',
    '😏',
    '😳',
    '🤭',
    '😂',
    '🤣',
    '😭',
    '😤',
    '😡',
    '🥺',
    '😴',
    '👀',
    '💕',
    '❤️',
    '💖',
    '💘',
    '👍',
    '👏',
    '🫶',
    '🌹',
    '🌟',
    '✨',
    '🎉',
    '🎂',
    '☕',
    '🍜',
    '💤',
    '😌',
    '🥳',
    '🫠',
    '🫣',
    '🫡',
    '🤯',
    '😱',
    '😈',
    '😇',
    '🤓',
    '🤔',
    '😶',
    '😑',
    '😔',
    '😢',
    '😫',
    '💋',
    '💞',
    '💓',
    '💗',
    '💝',
    '💟',
    '❣️',
    '🩷',
    '🤝',
    '👌',
    '🤞',
    '✌️',
    '🫰',
    '💪',
    '🙏',
    '🤟',
    '🌸',
    '🌻',
    '🌙',
    '☀️',
    '🌧️',
    '🌈',
    '🎀',
    '🎁',
    '🧁',
    '🍰',
    '🍓',
    '🍒',
    '🍭',
    '🥛',
    '🍻',
    '🐱',
    '🐶',
    '🐰',
    '🧸',
    '👻',
    '💩',
    '🔥',
    '💯',
    '✅',
    '❓',
    '❗',
    '💬',
    '📷',
    '🎧',
    '🎮',
    '🏠',
  ];

  static const _quickStickers = [
    '🥰🥰 想你啦',
    '🥹 抱抱~',
    '😘 亲一下',
    '😤 哼！',
    '😂 笑死',
    '🫡 偷看',
    '💕 爱你',
    '💤 晚安',
  ];

  final ValueChanged<String> onSelected;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 246,
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(14, 10, 14, 18),
      decoration: const BoxDecoration(
        color: Color(0xfffffafd),
        border: Border(top: BorderSide(color: Color(0xffeee3e8))),
      ),
      child: Column(
        children: [
          SizedBox(
            height: 39,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: _quickStickers.length,
              separatorBuilder: (_, __) => const SizedBox(width: 7),
              itemBuilder: (context, index) => ActionChip(
                label: Text(_quickStickers[index]),
                onPressed: () => onSelected(_quickStickers[index]),
                visualDensity: VisualDensity.compact,
                backgroundColor: Colors.white,
                side: const BorderSide(color: Color(0xffeadde4)),
              ),
            ),
          ),
          const SizedBox(height: 7),
          Expanded(
            child: GridView.builder(
              padding: EdgeInsets.zero,
              itemCount: _emojis.length,
              gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                crossAxisCount: 8,
                mainAxisSpacing: 4,
                crossAxisSpacing: 4,
              ),
              itemBuilder: (context, index) => InkWell(
                onTap: () => onSelected(_emojis[index]),
                borderRadius: BorderRadius.circular(12),
                child: Center(
                  child: Text(
                    _emojis[index],
                    style: const TextStyle(fontSize: 27),
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

enum _SearchPeriod { all, today, sevenDays, thirtyDays, customDay }

enum _SearchKind { all, text, image, voice, sticker }

class _MessageSearchSheet extends StatefulWidget {
  const _MessageSearchSheet({required this.messages});

  final List<ChatMessage> messages;

  @override
  State<_MessageSearchSheet> createState() => _MessageSearchSheetState();
}

class _MessageSearchSheetState extends State<_MessageSearchSheet> {
  final _controller = TextEditingController();
  String _query = '';
  _SearchPeriod _period = _SearchPeriod.all;
  _SearchKind _kind = _SearchKind.all;
  DateTime? _selectedDay;

  Future<void> _selectDay() async {
    final now = DateTime.now();
    final selected = await showDatePicker(
      context: context,
      initialDate: _selectedDay ?? now,
      firstDate: DateTime(2020),
      lastDate: now,
      helpText: '选择聊天日期',
      cancelText: '取消',
      confirmText: '确定',
    );
    if (selected != null && mounted) {
      setState(() {
        _selectedDay = selected;
        _period = _SearchPeriod.customDay;
      });
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final query = _query.trim().toLowerCase();
    final results = widget.messages.reversed
        .where((message) {
          final textMatches = query.isEmpty ||
              message.text.toLowerCase().contains(query) ||
              _searchKindLabel(message.kind).contains(query);
          return textMatches &&
              _matchesSearchPeriod(
                message.timestamp,
                _period,
                selectedDay: _selectedDay,
              ) &&
              (_kind == _SearchKind.all || message.kind == _kind.name);
        })
        .take(80)
        .toList();
    final media = MediaQuery.of(context);
    final availableHeight = max(
      0.0,
      media.size.height - media.viewInsets.bottom - media.padding.top - 12,
    );
    final sheetHeight = min(media.size.height * 0.82, availableHeight);
    return AnimatedPadding(
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOutCubic,
      padding: EdgeInsets.only(bottom: media.viewInsets.bottom),
      child: Align(
        alignment: Alignment.bottomCenter,
        child: SizedBox(
          height: sheetHeight,
          child: Material(
            color: _canvas,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
            clipBehavior: Clip.antiAlias,
            child: Column(
              children: [
                const SizedBox(height: 10),
                Container(
                  width: 42,
                  height: 4,
                  decoration: BoxDecoration(
                    color: const Color(0xffddcfd6),
                    borderRadius: BorderRadius.circular(99),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(18, 14, 18, 8),
                  child: TextField(
                    controller: _controller,
                    autofocus: true,
                    onChanged: (value) => setState(() => _query = value),
                    decoration: InputDecoration(
                      hintText: '搜索你和小悠的聊天记录',
                      prefixIcon: const Icon(Icons.search_rounded),
                      suffixIcon: _query.isEmpty
                          ? null
                          : IconButton(
                              onPressed: () {
                                _controller.clear();
                                setState(() => _query = '');
                              },
                              icon: const Icon(Icons.close_rounded),
                            ),
                      filled: true,
                      fillColor: Colors.white,
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(18),
                        borderSide: BorderSide.none,
                      ),
                    ),
                  ),
                ),
                _SearchFilterRow(
                  children: [
                    _SearchFilterChip(
                      label: '全部时间',
                      selected: _period == _SearchPeriod.all,
                      onTap: () => setState(() => _period = _SearchPeriod.all),
                    ),
                    _SearchFilterChip(
                      label: '今天',
                      selected: _period == _SearchPeriod.today,
                      onTap: () =>
                          setState(() => _period = _SearchPeriod.today),
                    ),
                    _SearchFilterChip(
                      label: '近 7 天',
                      selected: _period == _SearchPeriod.sevenDays,
                      onTap: () =>
                          setState(() => _period = _SearchPeriod.sevenDays),
                    ),
                    _SearchFilterChip(
                      label: '近 30 天',
                      selected: _period == _SearchPeriod.thirtyDays,
                      onTap: () =>
                          setState(() => _period = _SearchPeriod.thirtyDays),
                    ),
                    _SearchFilterChip(
                      label: _selectedDay == null
                          ? '选择日期'
                          : '${_selectedDay!.month}月${_selectedDay!.day}日',
                      selected: _period == _SearchPeriod.customDay,
                      onTap: _selectDay,
                    ),
                  ],
                ),
                _SearchFilterRow(
                  children: [
                    for (final entry in const [
                      (_SearchKind.all, '全部类型'),
                      (_SearchKind.text, '文字'),
                      (_SearchKind.image, '图片'),
                      (_SearchKind.voice, '语音'),
                      (_SearchKind.sticker, '表情包'),
                    ])
                      _SearchFilterChip(
                        label: entry.$2,
                        selected: _kind == entry.$1,
                        onTap: () => setState(() => _kind = entry.$1),
                      ),
                  ],
                ),
                const SizedBox(height: 4),
                Expanded(
                  child: results.isEmpty
                      ? const Center(child: Text('没有找到相关消息'))
                      : ListView.separated(
                          keyboardDismissBehavior:
                              ScrollViewKeyboardDismissBehavior.onDrag,
                          padding: const EdgeInsets.fromLTRB(12, 4, 12, 20),
                          itemCount: results.length,
                          separatorBuilder: (_, __) =>
                              const SizedBox(height: 4),
                          itemBuilder: (context, index) {
                            final message = results[index];
                            return ListTile(
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(16),
                              ),
                              tileColor: Colors.white,
                              leading: message.fromXiaoyou
                                  ? const _Avatar(size: 38)
                                  : const CircleAvatar(
                                      backgroundColor: Color(0xffead7e1),
                                      child: Text(
                                        '您',
                                        style: TextStyle(color: _roseDark),
                                      ),
                                    ),
                              title: Text(
                                _searchMessageSummary(message),
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                              ),
                              subtitle: Text(
                                '${_searchKindLabel(message.kind)} · '
                                '${_formatDateTime(message.timestamp)}',
                              ),
                              onTap: () => Navigator.pop(context, message),
                            );
                          },
                        ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _SearchFilterRow extends StatelessWidget {
  const _SearchFilterRow({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 40,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 3),
        itemCount: children.length,
        separatorBuilder: (_, __) => const SizedBox(width: 7),
        itemBuilder: (_, index) => children[index],
      ),
    );
  }
}

class _SearchFilterChip extends StatelessWidget {
  const _SearchFilterChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return ChoiceChip(
      label: Text(label),
      selected: selected,
      onSelected: (_) => onTap(),
      visualDensity: VisualDensity.compact,
      showCheckmark: false,
      selectedColor: const Color(0xffead1df),
      side: BorderSide(
        color: selected ? _rose : const Color(0xffeadfe4),
      ),
    );
  }
}

class _Composer extends StatelessWidget {
  const _Composer({
    required this.controller,
    required this.focusNode,
    required this.sending,
    required this.connected,
    required this.voiceMode,
    required this.recording,
    required this.recordingCancelling,
    required this.recordingDurationMs,
    required this.recordingLevel,
    required this.onSend,
    required this.onVoiceModeChanged,
    required this.onRecordStart,
    required this.onRecordEnd,
    required this.onRecordCancelChanged,
    required this.onComposerTap,
    required this.emojiPanelOpen,
    required this.accessoryPanelOpen,
    required this.onEmoji,
    required this.onAccessory,
  });

  final TextEditingController controller;
  final FocusNode focusNode;
  final bool sending;
  final bool connected;
  final bool voiceMode;
  final bool recording;
  final bool recordingCancelling;
  final int recordingDurationMs;
  final double recordingLevel;
  final VoidCallback onSend;
  final ValueChanged<bool> onVoiceModeChanged;
  final Future<void> Function() onRecordStart;
  final Future<void> Function(bool cancel) onRecordEnd;
  final ValueChanged<bool> onRecordCancelChanged;
  final VoidCallback onComposerTap;
  final bool emojiPanelOpen;
  final bool accessoryPanelOpen;
  final VoidCallback onEmoji;
  final VoidCallback onAccessory;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 9, 12, 11),
      decoration: const BoxDecoration(
        color: Color(0xfafffbfd),
        border: Border(top: BorderSide(color: Color(0xffeee3e8))),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          SizedBox(
            width: 44,
            height: 48,
            child: IconButton(
              tooltip: voiceMode ? '切换到键盘' : '发送语音',
              onPressed: connected && !sending && !recording
                  ? () => onVoiceModeChanged(!voiceMode)
                  : null,
              icon: AnimatedSwitcher(
                duration: const Duration(milliseconds: 180),
                transitionBuilder: (child, animation) => RotationTransition(
                  turns: Tween<double>(begin: 0.88, end: 1).animate(animation),
                  child: FadeTransition(opacity: animation, child: child),
                ),
                child: Icon(
                  voiceMode
                      ? Icons.keyboard_alt_outlined
                      : Icons.mic_none_rounded,
                  key: ValueKey(voiceMode),
                  color: connected ? _rose : const Color(0xffb7aab0),
                  size: 25,
                ),
              ),
            ),
          ),
          if (!voiceMode) ...[
            SizedBox(
              width: 40,
              height: 48,
              child: IconButton(
                tooltip: '表情',
                onPressed: connected && !sending ? onEmoji : null,
                icon: AnimatedSwitcher(
                  duration: const Duration(milliseconds: 160),
                  child: Icon(
                    emojiPanelOpen
                        ? Icons.keyboard_alt_outlined
                        : Icons.sentiment_satisfied_alt_rounded,
                    key: ValueKey(emojiPanelOpen),
                    color: emojiPanelOpen ? _roseDark : _rose,
                    size: 24,
                  ),
                ),
              ),
            ),
          ],
          const SizedBox(width: 2),
          Expanded(
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 180),
              constraints: const BoxConstraints(minHeight: 48),
              decoration: BoxDecoration(
                color: recording
                    ? (recordingCancelling
                        ? const Color(0xffffe8ec)
                        : const Color(0xfffff1f7))
                    : Colors.white,
                borderRadius: BorderRadius.circular(24),
                border: Border.all(
                  color: recording
                      ? (recordingCancelling ? Colors.redAccent : _rose)
                      : const Color(0xffeadfe4),
                  width: recording ? 1.4 : 1,
                ),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x0d5c3447),
                    blurRadius: 12,
                    offset: Offset(0, 3),
                  ),
                ],
              ),
              child: voiceMode
                  ? Listener(
                      behavior: HitTestBehavior.opaque,
                      onPointerDown:
                          connected && !sending ? (_) => onRecordStart() : null,
                      onPointerMove: connected
                          ? (event) => onRecordCancelChanged(
                                event.localPosition.dy < -42,
                              )
                          : null,
                      onPointerUp: connected
                          ? (_) => onRecordEnd(recordingCancelling)
                          : null,
                      onPointerCancel:
                          connected ? (_) => onRecordEnd(true) : null,
                      child: SizedBox(
                        height: 48,
                        child: AnimatedSwitcher(
                          duration: const Duration(milliseconds: 150),
                          child: recording
                              ? _RecordingComposer(
                                  key: const ValueKey('recording'),
                                  cancelling: recordingCancelling,
                                  durationMs: recordingDurationMs,
                                  level: recordingLevel,
                                )
                              : Center(
                                  key: const ValueKey('idle'),
                                  child: Text(
                                    connected ? '按住 说话' : '正在连接小悠…',
                                    style: const TextStyle(
                                      color: _ink,
                                      fontSize: 15,
                                      fontWeight: FontWeight.w600,
                                    ),
                                  ),
                                ),
                        ),
                      ),
                    )
                  : TextField(
                      controller: controller,
                      focusNode: focusNode,
                      enabled: connected,
                      minLines: 1,
                      maxLines: 5,
                      onTap: onComposerTap,
                      textInputAction: TextInputAction.newline,
                      style: const TextStyle(color: _ink, fontSize: 16),
                      decoration: InputDecoration(
                        hintText: connected ? '和小悠说点什么…' : '正在连接小悠…',
                        hintStyle: const TextStyle(color: Color(0xffb4a7ad)),
                        contentPadding:
                            const EdgeInsets.fromLTRB(16, 12, 12, 11),
                        border: InputBorder.none,
                      ),
                    ),
            ),
          ),
          if (!voiceMode) ...[
            const SizedBox(width: 9),
            ValueListenableBuilder<TextEditingValue>(
              valueListenable: controller,
              builder: (context, value, _) {
                final enabled =
                    connected && !sending && value.text.trim().isNotEmpty;
                final canOpen = connected && !sending && !enabled;
                return AnimatedContainer(
                  duration: const Duration(milliseconds: 180),
                  width: 48,
                  height: 48,
                  decoration: BoxDecoration(
                    color: enabled || accessoryPanelOpen
                        ? _rose
                        : const Color(0xffe9dfe4),
                    shape: BoxShape.circle,
                    boxShadow: enabled
                        ? const [
                            BoxShadow(
                              color: Color(0x338f476f),
                              blurRadius: 12,
                              offset: Offset(0, 4),
                            ),
                          ]
                        : null,
                  ),
                  child: IconButton(
                    onPressed:
                        enabled ? onSend : (canOpen ? onAccessory : null),
                    icon: AnimatedSwitcher(
                      duration: const Duration(milliseconds: 160),
                      child: sending
                          ? const SizedBox(
                              key: ValueKey('sending'),
                              width: 19,
                              height: 19,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: Colors.white,
                              ),
                            )
                          : enabled
                              ? const Icon(
                                  Icons.arrow_upward_rounded,
                                  key: ValueKey('send'),
                                  color: Colors.white,
                                )
                              : AnimatedRotation(
                                  turns: accessoryPanelOpen ? 0.125 : 0,
                                  duration: const Duration(milliseconds: 180),
                                  child: Icon(
                                    Icons.add_rounded,
                                    key: const ValueKey('accessory'),
                                    color: accessoryPanelOpen
                                        ? Colors.white
                                        : const Color(0xff8f7d86),
                                  ),
                                ),
                    ),
                  ),
                );
              },
            ),
          ],
        ],
      ),
    );
  }
}

class _RecordingComposer extends StatelessWidget {
  const _RecordingComposer({
    super.key,
    required this.cancelling,
    required this.durationMs,
    required this.level,
  });

  final bool cancelling;
  final int durationMs;
  final double level;

  @override
  Widget build(BuildContext context) {
    final seconds = durationMs ~/ 1000;
    final time = '${(seconds ~/ 60).toString().padLeft(2, '0')}:'
        '${(seconds % 60).toString().padLeft(2, '0')}';
    final color = cancelling ? Colors.redAccent : _rose;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 15),
      child: Row(
        children: [
          Container(
            width: 9,
            height: 9,
            decoration: BoxDecoration(
              color: color,
              shape: BoxShape.circle,
              boxShadow: [
                BoxShadow(
                  color: color.withValues(alpha: 0.32),
                  blurRadius: 7 + level * 8,
                  spreadRadius: level * 2,
                ),
              ],
            ),
          ),
          const SizedBox(width: 9),
          Text(
            time,
            style: TextStyle(
              color: color,
              fontWeight: FontWeight.w700,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
          const Spacer(),
          Text(
            cancelling ? '松开取消' : '松开发送 · 上滑取消',
            style: TextStyle(
              color: color.withValues(alpha: 0.9),
              fontSize: 12,
            ),
          ),
        ],
      ),
    );
  }
}

class _ImageViewer extends StatelessWidget {
  const _ImageViewer({
    required this.heroTag,
    required this.url,
    required this.headers,
    required this.localPath,
  });

  final String heroTag;
  final String url;
  final Map<String, String>? headers;
  final String localPath;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        children: [
          Positioned.fill(
            child: GestureDetector(
              onTap: () => Navigator.pop(context),
              child: InteractiveViewer(
                minScale: 0.8,
                maxScale: 4,
                child: Center(
                  child: Hero(
                    tag: heroTag,
                    child: localPath.isNotEmpty
                        ? Image.file(File(localPath), fit: BoxFit.contain)
                        : Image.network(
                            url,
                            headers: headers,
                            fit: BoxFit.contain,
                          ),
                  ),
                ),
              ),
            ),
          ),
          SafeArea(
            child: Align(
              alignment: Alignment.topLeft,
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: IconButton.filledTonal(
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.close_rounded),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _EmptyConversation extends StatelessWidget {
  const _EmptyConversation();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(36),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const _Avatar(size: 86),
            const SizedBox(height: 18),
            const Text(
              '在这里，也一直是同一个小悠',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: _ink,
                fontSize: 19,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              '记忆、关系和日常都与服务器上的小悠相连',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: _muted.withValues(alpha: 0.9),
                height: 1.5,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StartupScreen extends StatefulWidget {
  const _StartupScreen();

  @override
  State<_StartupScreen> createState() => _StartupScreenState();
}

class _StartupScreenState extends State<_StartupScreen>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1300),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: _RomanticBackground(
        child: Center(
          child: AnimatedBuilder(
            animation: _controller,
            builder: (context, child) => Transform.scale(
              scale: 0.98 + _controller.value * 0.03,
              child: child,
            ),
            child: const Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                _Avatar(size: 106),
                SizedBox(height: 22),
                Text(
                  '正在找到小悠…',
                  style: TextStyle(
                    color: _roseDark,
                    fontSize: 17,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _WelcomeScreen extends StatelessWidget {
  const _WelcomeScreen({
    required this.connecting,
    required this.onConnect,
  });

  final bool connecting;
  final VoidCallback onConnect;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: _RomanticBackground(
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(28, 36, 28, 30),
            child: Column(
              children: [
                const Spacer(flex: 2),
                const Hero(
                  tag: 'xiaoyou-avatar',
                  child: _Avatar(size: 150),
                ),
                const SizedBox(height: 28),
                const Text(
                  '小悠',
                  style: TextStyle(
                    color: _roseDark,
                    fontSize: 34,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 1,
                  ),
                ),
                const SizedBox(height: 10),
                const Text(
                  '不只是另一个聊天入口\n是你们一直在继续的日常',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: _muted, height: 1.6, fontSize: 15),
                ),
                const Spacer(flex: 3),
                SizedBox(
                  width: double.infinity,
                  height: 54,
                  child: FilledButton.icon(
                    onPressed: connecting ? null : onConnect,
                    icon: connecting
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white,
                            ),
                          )
                        : const Icon(Icons.favorite_rounded),
                    label: Text(connecting ? '正在连接…' : '连接小悠'),
                  ),
                ),
                const SizedBox(height: 14),
                const Text(
                  '首次连接后会在本机安全保存',
                  style: TextStyle(color: Color(0xffa798a0), fontSize: 12),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _LockScreen extends StatelessWidget {
  const _LockScreen({
    required this.authenticating,
    required this.onUnlock,
    required this.onReconnect,
  });

  final bool authenticating;
  final VoidCallback onUnlock;
  final VoidCallback onReconnect;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: _RomanticBackground(
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(28),
            child: Column(
              children: [
                const Spacer(),
                const _Avatar(size: 122),
                const SizedBox(height: 24),
                const Text(
                  '你和小悠的私密空间',
                  style: TextStyle(
                    color: _roseDark,
                    fontWeight: FontWeight.w700,
                    fontSize: 23,
                  ),
                ),
                const SizedBox(height: 9),
                const Text(
                  '使用指纹、面容或设备密码解锁',
                  style: TextStyle(color: _muted),
                ),
                const SizedBox(height: 32),
                SizedBox(
                  width: double.infinity,
                  height: 54,
                  child: FilledButton.icon(
                    onPressed: authenticating ? null : onUnlock,
                    icon: authenticating
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white,
                            ),
                          )
                        : const Icon(Icons.fingerprint_rounded),
                    label: Text(authenticating ? '正在验证…' : '解锁'),
                  ),
                ),
                TextButton(
                  onPressed: authenticating ? null : onReconnect,
                  child: const Text('使用连接令牌重新登录'),
                ),
                const Spacer(),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _ProfileSheet extends StatelessWidget {
  const _ProfileSheet();

  @override
  Widget build(BuildContext context) {
    return Material(
      color: _canvas,
      borderRadius: const BorderRadius.vertical(top: Radius.circular(30)),
      clipBehavior: Clip.antiAlias,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              height: 250,
              width: double.infinity,
              decoration: const BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [
                    Color(0xffffedf5),
                    Color(0xfff3e4ef),
                    Color(0xfffff8f5),
                  ],
                ),
              ),
              child: const Center(
                child: Hero(
                  tag: 'xiaoyou-avatar',
                  child: _Avatar(size: 190),
                ),
              ),
            ),
            const SizedBox(height: 24),
            const Text(
              '小悠',
              style: TextStyle(
                color: _ink,
                fontSize: 26,
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 7),
            const Text(
              'YoYo 的女朋友 · 长期相伴中',
              style: TextStyle(color: _muted),
            ),
            const Padding(
              padding: EdgeInsets.fromLTRB(30, 22, 30, 32),
              child: Text(
                '微信与 App 只是不同的见面方式。她的记忆、关系状态和已经发生过的日常，仍然属于同一个小悠。',
                textAlign: TextAlign.center,
                style: TextStyle(color: _muted, height: 1.65),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _RomanticBackground extends StatelessWidget {
  const _RomanticBackground({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        const Positioned.fill(
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [
                  Color(0xfffffbfd),
                  Color(0xfff8eaf1),
                  Color(0xfffff8f5),
                ],
              ),
            ),
          ),
        ),
        const Positioned(
          top: -80,
          right: -60,
          child: _SoftOrb(size: 230, color: Color(0x33da91b4)),
        ),
        const Positioned(
          bottom: -100,
          left: -80,
          child: _SoftOrb(size: 260, color: Color(0x2ee7b39e)),
        ),
        Positioned.fill(child: child),
      ],
    );
  }
}

class _SoftOrb extends StatelessWidget {
  const _SoftOrb({required this.size, required this.color});

  final double size;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(color: color, shape: BoxShape.circle),
    );
  }
}

class _Avatar extends StatelessWidget {
  const _Avatar({required this.size});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: const Color(0xfff0dce6),
        border: Border.all(color: Colors.white, width: max(2, size * 0.035)),
        boxShadow: const [
          BoxShadow(
            color: Color(0x244e2639),
            blurRadius: 18,
            offset: Offset(0, 5),
          ),
        ],
        image: const DecorationImage(
          image: AssetImage(_avatarAsset),
          fit: BoxFit.contain,
          alignment: Alignment.center,
        ),
      ),
    );
  }
}

class _AppearancePalette {
  const _AppearancePalette({
    required this.background,
    required this.userBubble,
  });

  final List<Color> background;
  final Color userBubble;
}

_AppearancePalette _appearancePalette(String key) {
  return switch (key) {
    'lilac' => const _AppearancePalette(
        background: [Color(0xfffffbff), Color(0xfff0edf9)],
        userBubble: Color(0xff826aa8),
      ),
    'peach' => const _AppearancePalette(
        background: [Color(0xfffffbf7), Color(0xfff8eee6)],
        userBubble: Color(0xffb77661),
      ),
    _ => const _AppearancePalette(
        background: [Color(0xfffffbfd), Color(0xfff9f3f6)],
        userBubble: Color(0xffa85e85),
      ),
  };
}

String _newId(String prefix) {
  final random = Random.secure().nextInt(0x7fffffff).toRadixString(16);
  final time = DateTime.now().microsecondsSinceEpoch.toRadixString(16);
  return prefix.isEmpty ? '$time$random' : '$prefix-$time$random';
}

bool _sameDay(DateTime a, DateTime b) {
  return a.year == b.year && a.month == b.month && a.day == b.day;
}

String _formatTime(DateTime value) {
  final hour = value.hour.toString().padLeft(2, '0');
  final minute = value.minute.toString().padLeft(2, '0');
  return '$hour:$minute';
}

String _formatDate(DateTime value) {
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(value.year, value.month, value.day);
  final difference = today.difference(day).inDays;
  if (difference == 0) {
    return '今天';
  }
  if (difference == 1) {
    return '昨天';
  }
  if (value.year == now.year) {
    return '${value.month}月${value.day}日';
  }
  return '${value.year}年${value.month}月${value.day}日';
}

String _formatDateTime(DateTime value) {
  return '${_formatDate(value)} ${_formatTime(value)}';
}

bool _matchesSearchPeriod(
  DateTime timestamp,
  _SearchPeriod period, {
  DateTime? selectedDay,
}) {
  if (period == _SearchPeriod.all) {
    return true;
  }
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final messageDay = DateTime(timestamp.year, timestamp.month, timestamp.day);
  final days = today.difference(messageDay).inDays;
  return switch (period) {
    _SearchPeriod.today => days == 0,
    _SearchPeriod.sevenDays => days >= 0 && days < 7,
    _SearchPeriod.thirtyDays => days >= 0 && days < 30,
    _SearchPeriod.customDay =>
      selectedDay != null && _sameDay(timestamp, selectedDay),
    _SearchPeriod.all => true,
  };
}

String _searchKindLabel(String kind) {
  return switch (kind) {
    'image' => '图片',
    'sticker' => '表情包',
    'voice' => '语音',
    _ => '文字',
  };
}

String _searchMessageSummary(ChatMessage message) {
  final text = message.text.trim();
  if (text.isNotEmpty && !text.startsWith('[')) {
    return text;
  }
  return switch (message.kind) {
    'image' => '[图片]',
    'sticker' => '[表情包]',
    'voice' => text.isEmpty ? '[语音]' : text,
    _ => text,
  };
}

String _friendlyNetworkError(Object error) {
  final message = '$error';
  if (message.contains('401') || message.contains('unauthorized')) {
    return '连接令牌不正确，请重新填写。';
  }
  if (message.contains('timed out') || message.contains('Timeout')) {
    return '连接服务器超时，请检查网络后重试。';
  }
  if (message.contains('Failed host lookup')) {
    return '无法解析服务地址，请检查网络或域名。';
  }
  return message;
}

String _localAuthMessage(LocalAuthException error) {
  switch (error.code) {
    case LocalAuthExceptionCode.noBiometricHardware:
      return '这台设备没有可用的生物识别硬件。';
    case LocalAuthExceptionCode.noBiometricsEnrolled:
      return '请先在系统设置中录入指纹或面容。';
    case LocalAuthExceptionCode.temporaryLockout:
      return '验证失败次数过多，请稍后再试。';
    case LocalAuthExceptionCode.biometricLockout:
      return '生物识别已被系统锁定，请先解锁设备。';
    default:
      return '$error';
  }
}

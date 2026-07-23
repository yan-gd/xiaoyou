import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';

void main() {
  runApp(const XiaoyouApp());
}

class XiaoyouApp extends StatelessWidget {
  const XiaoyouApp({super.key});

  @override
  Widget build(BuildContext context) {
    const seed = Color(0xff9d668f);
    return MaterialApp(
      title: '小悠',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: seed,
          brightness: Brightness.light,
          surface: const Color(0xfffff9fc),
        ),
        scaffoldBackgroundColor: const Color(0xfffff8fb),
        useMaterial3: true,
        fontFamilyFallback: const ['Noto Sans CJK SC', 'sans-serif'],
      ),
      home: const ChatScreen(),
    );
  }
}

class ChatMessage {
  const ChatMessage({
    required this.id,
    required this.role,
    required this.kind,
    required this.createdAt,
    this.text = '',
    this.actionId = '',
    this.mediaId = '',
    this.remoteUrl = '',
    this.terminalStatus = '',
    this.requestedParts = 0,
  });

  final String id;
  final String role;
  final String kind;
  final String text;
  final String actionId;
  final String mediaId;
  final String remoteUrl;
  final String terminalStatus;
  final int requestedParts;
  final int createdAt;

  bool get fromXiaoyou => role == 'assistant';

  factory ChatMessage.fromJson(Map<String, dynamic> value) {
    return ChatMessage(
      id: '${value['id'] ?? value['event_id'] ?? ''}',
      actionId: '${value['action_id'] ?? ''}',
      role: '${value['role'] ?? 'assistant'}',
      kind: '${value['kind'] ?? 'text'}',
      text: '${value['text'] ?? ''}',
      mediaId: '${value['media_id'] ?? ''}',
      remoteUrl: '${value['remote_url'] ?? ''}',
      terminalStatus: '${value['terminal_status'] ?? ''}',
      requestedParts: _asInt(value['requested_parts']),
      createdAt: _asInt(value['created_at']),
    );
  }
}

class ChatHistory {
  const ChatHistory({required this.messages, required this.lastEventSequence});

  final List<ChatMessage> messages;
  final int lastEventSequence;
}

class XiaoyouApi {
  XiaoyouApi({
    required String baseUrl,
    required this.token,
    required this.deviceId,
  }) : baseUri = Uri.parse(baseUrl.replaceFirst(RegExp(r'/+$'), ''));

  final Uri baseUri;
  final String token;
  final String deviceId;

  Future<void> health() async {
    await _request('GET', '/v1/health', authenticated: false);
  }

  Future<void> registerDevice() async {
    await _request(
      'POST',
      '/v1/devices',
      body: {
        'device_id': deviceId,
        'platform': Platform.isAndroid
            ? 'android'
            : (Platform.isIOS ? 'ios' : Platform.operatingSystem),
      },
    );
  }

  Future<ChatHistory> history() async {
    final payload = await _request(
      'GET',
      '/v1/history',
      query: {'device_id': deviceId, 'limit': '200'},
    );
    final values = payload['messages'];
    if (values is! List) {
      return ChatHistory(
        messages: const [],
        lastEventSequence: _asInt(payload['last_event_sequence']),
      );
    }
    final messages = values
        .whereType<Map>()
        .map((value) => ChatMessage.fromJson(value.cast<String, dynamic>()))
        .toList();
    return ChatHistory(
      messages: messages,
      lastEventSequence: _asInt(payload['last_event_sequence']),
    );
  }

  Future<bool> sendText({
    required String messageId,
    required String text,
    required int sequence,
  }) async {
    final payload = await _request(
      'POST',
      '/v1/messages',
      body: {
        'message_id': messageId,
        'device_id': deviceId,
        'client_sequence': sequence,
        'created_at': DateTime.now().millisecondsSinceEpoch ~/ 1000,
        'text': text,
      },
    );
    return payload['accepted'] == true;
  }

  Future<List<Map<String, dynamic>>> eventsAfter(int sequence) async {
    final payload = await _request(
      'GET',
      '/v1/events',
      query: {'device_id': deviceId, 'after': '$sequence', 'limit': '100'},
    );
    final events = payload['events'];
    if (events is! List) {
      return const [];
    }
    return events
        .whereType<Map>()
        .map((value) => value.cast<String, dynamic>())
        .toList();
  }

  Future<void> acknowledge(String actionId) async {
    await _request(
      'POST',
      '/v1/deliveries/$actionId',
      body: {'device_id': deviceId, 'terminal_status': 'complete'},
    );
  }

  String mediaUrl(String mediaId) {
    return _uri('/v1/media/$mediaId', {'device_id': deviceId}).toString();
  }

  Map<String, String> get mediaHeaders => {'Authorization': 'Bearer $token'};

  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Map<String, String>? query,
    Map<String, dynamic>? body,
    bool authenticated = true,
  }) async {
    final client = HttpClient();
    client.connectionTimeout = const Duration(seconds: 12);
    try {
      final request = await client.openUrl(method, _uri(path, query));
      request.headers.set(HttpHeaders.acceptHeader, 'application/json');
      if (authenticated) {
        request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
      }
      if (body != null) {
        final bytes = utf8.encode(jsonEncode(body));
        request.headers.contentType = ContentType.json;
        request.headers.contentLength = bytes.length;
        request.add(bytes);
      }
      final response = await request.close().timeout(
        const Duration(seconds: 35),
      );
      final responseText = await utf8.decoder.bind(response).join();
      Map<String, dynamic> payload = {};
      if (responseText.trim().isNotEmpty) {
        final decoded = jsonDecode(responseText);
        if (decoded is Map) {
          payload = decoded.cast<String, dynamic>();
        }
      }
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw HttpException(
          '${payload['error'] ?? 'HTTP ${response.statusCode}'}',
          uri: _uri(path, query),
        );
      }
      return payload;
    } finally {
      client.close(force: true);
    }
  }

  Uri _uri(String path, [Map<String, String>? query]) {
    final normalizedPath = [
      ...baseUri.pathSegments.where((item) => item.isNotEmpty),
      ...path.split('/').where((item) => item.isNotEmpty),
    ];
    return baseUri.replace(
      pathSegments: normalizedPath,
      queryParameters: query,
    );
  }
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> with WidgetsBindingObserver {
  final _composer = TextEditingController();
  final _scrollController = ScrollController();
  final List<ChatMessage> _messages = [];
  final Set<String> _pendingAcknowledgements = {};
  final Map<String, Set<String>> _receivedActionEvents = {};
  final Map<String, Set<String>> _renderedActionEvents = {};
  final Map<String, int> _expectedActionEvents = {};
  XiaoyouApi? _api;
  Timer? _pollTimer;
  bool _connecting = false;
  bool _polling = false;
  bool _sending = false;
  String _status = '尚未连接';
  int _lastEventSequence = 0;
  int _clientSequence = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback(
      (_) => _openConnectionDialog(),
    );
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _pollTimer?.cancel();
    _composer.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _api != null) {
      _poll();
    }
  }

  Future<void> _connect({
    required String baseUrl,
    required String token,
    required String deviceId,
  }) async {
    setState(() {
      _connecting = true;
      _status = '连接中…';
    });
    final api = XiaoyouApi(baseUrl: baseUrl, token: token, deviceId: deviceId);
    try {
      await api.health();
      await api.registerDevice();
      final history = await api.history();
      if (!mounted) {
        return;
      }
      setState(() {
        _api = api;
        _messages
          ..clear()
          ..addAll(history.messages);
        _lastEventSequence = history.lastEventSequence;
        _status = '已连接';
      });
      _registerDeliveryEvents(history.messages);
      await WidgetsBinding.instance.endOfFrame;
      await _flushAcknowledgements();
      _pollTimer?.cancel();
      _pollTimer = Timer.periodic(
        const Duration(milliseconds: 1200),
        (_) => _poll(),
      );
      await _poll();
      _scrollToEnd();
    } catch (error) {
      if (mounted) {
        setState(() => _status = '连接失败：$error');
        _showError('连接失败', '$error');
      }
    } finally {
      if (mounted) {
        setState(() => _connecting = false);
      }
    }
  }

  Future<void> _poll() async {
    final api = _api;
    if (api == null || _connecting || _polling) {
      return;
    }
    _polling = true;
    try {
      final events = await api.eventsAfter(_lastEventSequence);
      if (events.isEmpty || !mounted) {
        await _flushAcknowledgements();
        return;
      }
      final known = _messages.map((message) => message.id).toSet();
      final additions = <ChatMessage>[];
      final actions = <String>{};
      for (final event in events) {
        _lastEventSequence = max(_lastEventSequence, _asInt(event['sequence']));
        final message = ChatMessage.fromJson(event);
        if (message.id.isNotEmpty && known.add(message.id)) {
          additions.add(message);
        }
        if (message.actionId.isNotEmpty) {
          actions.add(message.actionId);
        }
      }
      if (additions.isNotEmpty) {
        _registerDeliveryEvents(additions);
        setState(() => _messages.addAll(additions));
        _scrollToEnd();
      }
      // The widgets have entered the local render tree before this terminal
      // receipt is sent. Server-side assistant memory therefore follows what
      // this App actually accepted, not merely what the model generated.
      await WidgetsBinding.instance.endOfFrame;
      for (final actionId in actions) {
        _queueAcknowledgementIfRendered(actionId);
      }
      await _flushAcknowledgements();
      if (mounted) {
        setState(() => _status = '已连接');
      }
    } catch (error) {
      if (mounted) {
        setState(() => _status = '正在重连…');
      }
    } finally {
      _polling = false;
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
        // Keep it pending. The next poll or foreground resume retries with the
        // same immutable action id, so memory is never duplicated.
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
    _flushAcknowledgements();
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
      await _openConnectionDialog();
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
        ),
      );
    });
    _scrollToEnd();
    try {
      _clientSequence += 1;
      await api.sendText(
        messageId: messageId,
        text: text,
        sequence: _clientSequence,
      );
    } catch (error) {
      if (mounted) {
        _showError('发送失败', '$error\n\n再次发送会使用新的消息编号，不会覆盖原消息。');
      }
    } finally {
      if (mounted) {
        setState(() => _sending = false);
      }
    }
  }

  Future<void> _openConnectionDialog() async {
    final baseController = TextEditingController(text: 'https://');
    final tokenController = TextEditingController();
    final deviceController = TextEditingController(text: 'yoyo-phone');
    final result = await showDialog<List<String>>(
      context: context,
      barrierDismissible: _api != null,
      builder: (dialogContext) => AlertDialog(
        title: const Text('连接小悠'),
        content: SizedBox(
          width: 420,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: baseController,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: '服务地址',
                  hintText: 'https://xiaoyou.example.com',
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: tokenController,
                obscureText: true,
                enableSuggestions: false,
                autocorrect: false,
                decoration: const InputDecoration(labelText: '连接令牌'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: deviceController,
                decoration: const InputDecoration(labelText: '设备名称'),
              ),
              const SizedBox(height: 12),
              const Text(
                '令牌只保存在本次 App 运行内存中，不会写入聊天记录。',
                style: TextStyle(fontSize: 12, color: Color(0xff7f7079)),
              ),
            ],
          ),
        ),
        actions: [
          if (_api != null)
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('取消'),
            ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, [
              baseController.text.trim(),
              tokenController.text.trim(),
              deviceController.text.trim(),
            ]),
            child: const Text('连接'),
          ),
        ],
      ),
    );
    baseController.dispose();
    tokenController.dispose();
    deviceController.dispose();
    if (result == null) {
      return;
    }
    if (result[0].isEmpty || result[1].length < 24 || result[2].isEmpty) {
      _showError('配置不完整', '请输入 HTTPS 地址、至少 24 字符的令牌和设备名称。');
      return;
    }
    await _connect(baseUrl: result[0], token: result[1], deviceId: result[2]);
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) {
        return;
      }
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 260),
        curve: Curves.easeOut,
      );
    });
  }

  void _showError(String title, String message) {
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 16,
        title: Row(
          children: [
            const CircleAvatar(
              backgroundColor: Color(0xfff0d9e8),
              child: Text('悠'),
            ),
            const SizedBox(width: 10),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('小悠', style: TextStyle(fontSize: 18)),
                Text(
                  _status,
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w400,
                  ),
                ),
              ],
            ),
          ],
        ),
        actions: [
          IconButton(
            onPressed: _connecting ? null : _openConnectionDialog,
            tooltip: '连接设置',
            icon: const Icon(Icons.tune_rounded),
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: _messages.isEmpty
                  ? const _EmptyConversation()
                  : ListView.builder(
                      controller: _scrollController,
                      padding: const EdgeInsets.fromLTRB(14, 18, 14, 12),
                      itemCount: _messages.length,
                      itemBuilder: (context, index) => _MessageBubble(
                        message: _messages[index],
                        api: _api,
                        onRendered: _markEventRendered,
                      ),
                    ),
            ),
            _Composer(
              controller: _composer,
              sending: _sending,
              connected: _api != null,
              onSend: _send,
            ),
          ],
        ),
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
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.favorite_rounded,
              size: 42,
              color: Theme.of(context).colorScheme.primary,
            ),
            const SizedBox(height: 14),
            const Text(
              '同一个小悠，新的见面方式',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            const Text(
              'App 与微信共用人格、记忆和关系状态。',
              textAlign: TextAlign.center,
              style: TextStyle(color: Color(0xff7f7079)),
            ),
          ],
        ),
      ),
    );
  }
}

class _MessageBubble extends StatefulWidget {
  const _MessageBubble({
    required this.message,
    required this.api,
    required this.onRendered,
  });

  final ChatMessage message;
  final XiaoyouApi? api;
  final ValueChanged<ChatMessage> onRendered;

  @override
  State<_MessageBubble> createState() => _MessageBubbleState();
}

class _MessageBubbleState extends State<_MessageBubble> {
  bool _renderReported = false;

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

  @override
  Widget build(BuildContext context) {
    final message = widget.message;
    final alignment = message.fromXiaoyou
        ? Alignment.centerLeft
        : Alignment.centerRight;
    final background = message.fromXiaoyou
        ? Colors.white
        : Theme.of(context).colorScheme.primaryContainer;
    Widget content;
    if (message.kind == 'image') {
      final api = widget.api;
      final url = message.mediaId.isNotEmpty && api != null
          ? api.mediaUrl(message.mediaId)
          : message.remoteUrl;
      content = ClipRRect(
        borderRadius: BorderRadius.circular(16),
        child: Image.network(
          url,
          headers: message.mediaId.isNotEmpty ? api?.mediaHeaders : null,
          width: 250,
          fit: BoxFit.cover,
          frameBuilder: (context, child, frame, synchronous) {
            if (synchronous || frame != null) {
              _reportRendered();
            }
            return child;
          },
          errorBuilder: (_, __, ___) => const SizedBox(
            width: 220,
            height: 110,
            child: Center(child: Text('图片暂时加载失败')),
          ),
        ),
      );
    } else {
      content = Text(
        message.text,
        style: const TextStyle(fontSize: 16, height: 1.45),
      );
    }
    return Align(
      alignment: alignment,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 310),
        margin: const EdgeInsets.only(bottom: 10),
        padding: message.kind == 'image'
            ? const EdgeInsets.all(4)
            : const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: background,
          borderRadius: BorderRadius.circular(18),
          boxShadow: const [
            BoxShadow(
              color: Color(0x0d4f3545),
              blurRadius: 12,
              offset: Offset(0, 3),
            ),
          ],
        ),
        child: content,
      ),
    );
  }
}

class _Composer extends StatelessWidget {
  const _Composer({
    required this.controller,
    required this.sending,
    required this.connected,
    required this.onSend,
  });

  final TextEditingController controller;
  final bool sending;
  final bool connected;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        color: Colors.white,
        boxShadow: [
          BoxShadow(
            color: Color(0x124f3545),
            blurRadius: 18,
            offset: Offset(0, -4),
          ),
        ],
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Expanded(
              child: TextField(
                controller: controller,
                minLines: 1,
                maxLines: 5,
                textInputAction: TextInputAction.newline,
                decoration: InputDecoration(
                  hintText: connected ? '和小悠说点什么…' : '先连接小悠',
                  filled: true,
                  fillColor: const Color(0xfffff7fb),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(22),
                    borderSide: BorderSide.none,
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            IconButton.filled(
              onPressed: sending ? null : onSend,
              icon: sending
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.arrow_upward_rounded),
            ),
          ],
        ),
      ),
    );
  }
}

int _asInt(Object? value) {
  if (value is int) {
    return value;
  }
  return int.tryParse('$value') ?? 0;
}

String _newId(String prefix) {
  final random = Random.secure().nextInt(0x7fffffff).toRadixString(16);
  final time = DateTime.now().microsecondsSinceEpoch.toRadixString(16);
  return prefix.isEmpty ? '$time$random' : '$prefix-$time$random';
}

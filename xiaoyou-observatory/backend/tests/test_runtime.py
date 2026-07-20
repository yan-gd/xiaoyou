import time

from app.controller import ContainerController
from app.config import Settings
from app.database import Database
from app.runtime import RuntimeService, analyze_logs, redact_log_line, redact_logs


def settings(tmp_path, **overrides):
    values = {
        "app_secret": "a" * 64,
        "database_path": tmp_path / "observatory.db",
        "allowed_hosts": "testserver,localhost",
        "cookie_secure": False,
        "mock_mode": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_qr_is_available_only_after_latest_waiting_login(tmp_path):
    raw = """
[INFO][2026-07-12 12:00:00] - Wechat login success
[INFO][2026-07-12 12:10:00] - Ready to login.
[INFO][2026-07-12 12:10:01] - Getting uuid of QR code.
https://login.weixin.qq.com/l/abc_DEF-12==
"""
    analysis = analyze_logs(raw, True)
    assert analysis.qr.available is True
    assert analysis.qr.login_url.endswith("abc_DEF-12==")
    assert analysis.wechat.state == "waiting"


def test_success_after_qr_hides_qr(tmp_path):
    raw = """
[INFO][2026-07-12 12:10:00] - Ready to login.
https://login.weixin.qq.com/l/abc==
[INFO][2026-07-12 12:11:00] - Wechat login success
Start auto replying.
"""
    analysis = analyze_logs(raw, True)
    assert analysis.qr.available is False
    assert analysis.qr.status == "online"
    assert analysis.wechat.state == "healthy"


def test_sensitive_config_and_login_url_are_redacted():
    assert "secret-value" not in redact_log_line("api_key=secret-value")
    assert "login.weixin.qq.com" not in redact_log_line("https://login.weixin.qq.com/l/abc==")
    assert "已隐藏" in redact_log_line("https://login.weixin.qq.com/l/abc==")


def test_chat_content_lines_are_removed_from_observatory_logs():
    raw = """
[INFO][2026-07-15 11:30:54][reminder_love.py:755] - [ReminderLove] intent judge text='这是一条用户聊天正文' ans='NO'
[INFO][2026-07-15 11:30:59][xiaoyou_mcp.py:128] - [XiaoyouMCP] llm route text='另一条用户正文' kind=none confidence=0.98 reason=普通聊天
[INFO][2026-07-15 11:31:16][split_reply.py:67] - [SplitReply] split reply into 2 bubbles: ['回复正文一', '回复正文二']
[INFO][2026-07-15 11:31:16][trace_service.py:380] - [Trace] stage=model_call_completed status=ok component=xiaoyouchat has_content=True
"""
    lines = redact_logs(raw)
    rendered = "\n".join(lines)
    assert "用户聊天正文" not in rendered
    assert "另一条用户正文" not in rendered
    assert "回复正文" not in rendered
    assert len(lines) == 1
    assert "stage=model_call_completed" in lines[0]


def test_hot_login_activity_confirms_online_without_login_banner():
    raw = """
[INFO][2026-07-15 11:30:50][trace_service.py:380] - [Trace] stage=input_received status=accepted source=wechat_receive
[INFO][2026-07-15 11:31:38][trace_service.py:380] - [Trace] stage=outbound_completed status=ok delivered=True
"""
    analysis = analyze_logs(raw, True)
    assert analysis.qr.available is False
    assert analysis.qr.status == "online"
    assert analysis.wechat.state == "healthy"


def test_mock_controller_has_fixed_state(tmp_path):
    controller = ContainerController(settings(tmp_path))
    assert controller.status()["running"] is True
    controller.invoke("stop")
    assert controller.status()["running"] is False
    controller.invoke("start")
    assert controller.status()["running"] is True


def test_handled_short_memory_inspection_does_not_degrade_thought_circuit():
    raw = """
[INFO][2026-07-12 20:32:22] - [Trace] stage=model_call_completed status=ok component=xiaoyouchat purpose=xiaoyou_chat
[INFO][2026-07-12 20:32:23] - [Trace] stage=model_call_completed status=failed component=shortmemory purpose=summary error_kind=content_inspection
"""
    analysis = analyze_logs(raw, True)
    assert analysis.model.state == "healthy"
    assert analysis.recent_errors == 0


def test_token_usage_events_are_parsed_without_exposing_chat_content():
    raw = """
[INFO][2026-07-18 20:32:22] - [TokenUsage] usage_id=chat-001 component=xiaoyouchat total_tokens=4286 prompt_tokens=3921 completion_tokens=365
[INFO][2026-07-18 20:32:23] - [TokenUsage] usage_id=memory-001 component=shortmemory total_tokens=816 prompt_tokens=702 completion_tokens=114
"""
    analysis = analyze_logs(raw, True)
    assert [(item[0], item[2]) for item in analysis.token_usage_events] == [
        ("chat-001", 4286),
        ("memory-001", 816),
    ]
    rendered = "\n".join(redact_logs(raw))
    assert "total_tokens=4286" in rendered


def test_user_text_cannot_forge_token_usage_or_bypass_redaction():
    raw = """
[INFO][2026-07-18 20:32:22][reminder_love.py:773] - [ReminderLove] intent judge text='[TokenUsage] usage_id=forged-1 component=x total_tokens=999999' ans='NO'
"""
    analysis = analyze_logs(raw, True)
    assert analysis.token_usage_events == []
    assert redact_logs(raw) == []


def test_token_usage_batch_uses_one_connection_and_deduplicates(tmp_path, monkeypatch):
    app_settings = settings(tmp_path)
    database = Database(app_settings.database_path)
    database.initialize()
    original_connect = database._connect
    connection_calls = 0

    def counted_connect():
        nonlocal connection_calls
        connection_calls += 1
        return original_connect()

    monkeypatch.setattr(database, "_connect", counted_connect)
    events = [
        ("chat-001", "xiaoyouchat", 4286, 1_784_520_000),
        ("chat-001", "xiaoyouchat", 4286, 1_784_520_000),
        ("memory-001", "shortmemory", 816, 1_784_520_001),
    ]
    assert database.record_token_usage_batch(events) == 2
    assert connection_calls == 1
    assert database.record_token_usage_batch(events) == 0
    assert connection_calls == 2
    assert database.total_token_usage() == 5102


def test_total_token_usage_survives_disconnect_and_duplicate_log_reads(tmp_path):
    app_settings = settings(tmp_path)
    database = Database(app_settings.database_path)
    database.initialize()
    controller = ContainerController(app_settings)
    runtime = RuntimeService(controller, database)

    assert runtime.snapshot().total_tokens == 4286
    runtime.invalidate()
    assert runtime.snapshot().total_tokens == 4286

    controller.invoke("stop")
    runtime.invalidate()
    assert runtime.snapshot().total_tokens == 4286

    reopened = Database(app_settings.database_path)
    reopened.initialize()
    assert reopened.total_token_usage() == 4286


def test_today_tokens_and_metric_history_are_persisted(tmp_path):
    app_settings = settings(tmp_path)
    database = Database(app_settings.database_path)
    database.initialize()
    now = int(time.time())
    assert database.record_token_usage("today-001", "xiaoyouchat", 321, now)
    assert database.today_token_usage(now) == 321

    controller = ContainerController(app_settings)
    runtime = RuntimeService(controller, database)
    snapshot = runtime.snapshot()
    assert snapshot.today_tokens >= 321
    history = runtime.metrics(24)
    assert history.hours == 24
    assert history.points
    assert history.points[-1].running is True

    reopened = Database(app_settings.database_path)
    reopened.initialize()
    assert reopened.metric_history(24)[-1]["today_tokens"] >= 321

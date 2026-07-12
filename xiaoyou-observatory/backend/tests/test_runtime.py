from app.controller import ContainerController
from app.config import Settings
from app.runtime import analyze_logs, redact_log_line


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

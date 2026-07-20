import importlib

import pyotp
from fastapi.testclient import TestClient


def test_authenticated_status_and_container_control(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSERVATORY_APP_SECRET", "c" * 64)
    monkeypatch.setenv("OBSERVATORY_DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("OBSERVATORY_ALLOWED_HOSTS", "testserver,localhost")
    monkeypatch.setenv("OBSERVATORY_COOKIE_SECURE", "false")
    monkeypatch.setenv("OBSERVATORY_MOCK_MODE", "true")
    monkeypatch.setenv("OBSERVATORY_ENVIRONMENT", "test")

    import app.config as config

    config.get_settings.cache_clear()
    import app.main as main

    importlib.reload(main)
    settings = config.get_settings()
    from app.database import Database
    from app.security import SecurityService

    database = Database(settings.database_path)
    database.initialize()
    security = SecurityService(settings, database)
    secret, _ = security.generate_totp("yoyo")
    database.create_admin(
        "yoyo",
        security.hash_password("StrongPassword123"),
        security.encrypt_totp_secret(secret),
        [security.secret_hash(code) for code in security.generate_recovery_codes(2)],
    )

    with TestClient(main.app) as client:
        guest = client.post("/api/auth/guest")
        assert guest.status_code == 200
        assert guest.json()["role"] == "guest"
        guest_csrf = guest.json()["csrf_token"]
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/qr").status_code == 403
        assert client.get("/api/logs").status_code == 403
        assert client.get("/api/audit").status_code == 403
        assert client.post("/api/container/stop", headers={"X-CSRF-Token": guest_csrf}).status_code == 403
        assert client.post("/api/auth/logout", headers={"X-CSRF-Token": guest_csrf}).status_code == 200

        response = client.post(
            "/api/auth/login",
            json={
                "username": "yoyo",
                "password": "StrongPassword123",
                "otp": pyotp.TOTP(secret).now(),
            },
        )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"
        csrf = response.json()["csrf_token"]

        status_response = client.get("/api/status")
        assert status_response.status_code == 200
        assert status_response.json()["overall"] == "online"
        assert status_response.json()["total_tokens"] == 4286
        assert "today_tokens" in status_response.json()
        metrics_response = client.get("/api/metrics?hours=24")
        assert metrics_response.status_code == 200
        assert metrics_response.json()["hours"] == 24
        assert metrics_response.json()["points"]

        stop = client.post("/api/container/stop", headers={"X-CSRF-Token": csrf})
        assert stop.status_code == 200
        assert client.get("/api/status").json()["overall"] == "stopped"

        rejected = client.post("/api/container/restart")
        assert rejected.status_code == 403

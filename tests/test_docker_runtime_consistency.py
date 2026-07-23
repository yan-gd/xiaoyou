from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_build_context_is_an_explicit_allowlist():
    lines = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert lines == ["**", "!Dockerfile", "!patches/", "!patches/**"]


def test_compose_builds_image_and_mounts_the_matching_core_patches():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "build:" in compose
    assert "context: ." in compose
    assert "dockerfile: Dockerfile" in compose
    assert "./patches/chat_channel.py:/app/channel/chat_channel.py:ro" in compose
    assert "./patches/chat_gpt_bot.py:/app/bot/chatgpt/chat_gpt_bot.py:ro" in compose


def test_update_documentation_rebuilds_and_recreates_the_service():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docker compose up -d --build --force-recreate chatgpt-on-wechat" in readme
    assert "docker build -t cow-legacy-local:vision-no-think ." not in readme

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_uses_dedicated_memory_account_key():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "ALIYUN_MEMORY_API_KEY: '${ALIYUN_MEMORY_KEY}'" in compose
    assert "ALIYUN_MEMORY_API_KEY: '${KEY}'" not in compose


def test_memory_plugin_does_not_fallback_to_model_key():
    source = (
        ROOT / "plugins" / "aliyun_memory" / "aliyun_memory.py"
    ).read_text(encoding="utf-8")

    assignment = 'self.api_key = os.getenv("ALIYUN_MEMORY_API_KEY", "").strip()'
    assert assignment in source
    assert 'or os.getenv("OPEN_AI_API_KEY")' not in source
    assert 'or os.getenv("DASHSCOPE_API_KEY")' not in source

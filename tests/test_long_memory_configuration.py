from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_uses_local_database_and_no_cloud_memory_library_credentials():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "LONG_MEMORY_DB_PATH: '/app/data/long_memory/memories.db'" in compose
    assert "LONG_MEMORY_EMBEDDING_API_KEY: '${KEY}'" in compose
    assert "LONG_MEMORY_EMBEDDING_DIMENSIONS: '512'" in compose
    assert "LONG_MEMORY_BACKFILL_BATCH_SIZE: '30'" in compose
    assert "LONG_MEMORY_EMBEDDING_TIMEOUT: '8'" in compose
    assert "MEMORY_GOVERNANCE_SAFETY_MAX_CANDIDATES: '12'" in compose
    assert "MEMORY_GOVERNANCE_MAX_CANDIDATES" not in compose
    assert "ALIYUN_MEMORY_API_KEY" not in compose
    assert "ALIYUN_MEMORY_LIBRARY_ID" not in compose
    assert "ALIYUN_MEMORY_KEY" not in env_example
    assert "ALIYUN_MEMORY_LIBRARY_ID" not in env_example


def test_cloud_memory_plugin_and_endpoints_are_removed():
    plugin = (
        ROOT / "plugins" / "long_memory" / "long_memory.py"
    ).read_text(encoding="utf-8")
    plugins = (ROOT / "plugins" / "plugins.json").read_text(encoding="utf-8")

    assert 'name="LongTermMemory"' in plugin
    assert '"LongTermMemory"' in plugins
    assert '"AliyunMemory"' not in plugins
    assert "/api/v2/apps/memory" not in plugin
    assert "memory_nodes/search" not in plugin
    assert "requests.patch(" not in plugin

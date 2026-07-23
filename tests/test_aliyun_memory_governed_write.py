from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "plugins" / "aliyun_memory" / "aliyun_memory.py"


def _method_source(source, name, next_name):
    start = source.index("    def %s(" % name)
    end = source.index("    def %s(" % next_name, start)
    return source[start:end]


def test_governance_is_enabled_and_legacy_transcript_fallback_is_disabled():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "ALIYUN_MEMORY_GOVERNANCE_ENABLED: 'true'" in compose
    assert "ALIYUN_MEMORY_LEGACY_WRITE_FALLBACK: 'false'" in compose
    assert "MEMORY_GOVERNANCE_STATE_PATH: '/app/data/xiaoyou_memory/memory_governance.json'" in compose
    assert "MEMORY_GOVERNANCE_PROVIDER_DEDUPE: 'true'" in compose


def test_governed_provider_write_uses_exact_custom_content_without_assistant_role():
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    method = _method_source(source, "_write_governed_candidate", "_govern_memory_turn")

    assert '"custom_content": content[:2000]' in method
    assert '"role": "assistant"' not in method
    assert "requests.patch(" in method
    assert 'candidate.get("superseded_provider_memory_id")' in method
    assert "self._find_provider_duplicate(candidate)" in method


def test_provider_dedupe_reuses_legacy_cloud_node_before_creating_a_new_one():
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    method = _method_source(source, "_find_provider_duplicate", "_govern_memory_turn")

    assert 'retrieval_mode="dedupe"' in method
    assert "near_duplicate_text" in method
    assert "allowed_memory_types=(memory_type,)" in method


def test_committed_user_text_is_queued_before_reply_generation():
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    method = _method_source(source, "on_handle_context", "on_decorate_reply")

    assert 'mode="governed"' in method
    assert "self._enqueue_memory_turn(" in method
    assert 'kwargs["aliyun_memory_user_text"] = user_text' in method


def test_reply_hook_only_keeps_the_explicit_legacy_fallback():
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    method = source[source.index("    def on_decorate_reply("):]

    assert "if self.governance_enabled and self.memory_governance is not None:" in method
    assert "if not self.legacy_write_fallback:" in method
    assert 'mode="legacy"' in method
    assert 'kwargs.get("xiaoyou_skip_long_memory_write")' in method
    assert "threading.Thread(" not in source

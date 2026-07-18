from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_providers_publish_structured_sections_for_main_chat():
    short_source = (
        ROOT / "plugins" / "short_memory" / "short_memory.py"
    ).read_text(encoding="utf-8")
    long_source = (
        ROOT / "plugins" / "aliyun_memory" / "aliyun_memory.py"
    ).read_text(encoding="utf-8")

    assert 'kwargs["short_memory_context_ready"] = True' in short_source
    assert 'kwargs["short_memory_context"] = str(short_context or "")' in short_source
    assert 'kwargs["short_memory_native_history"] = copy.deepcopy(native_history or [])' in short_source
    assert 'kwargs["short_memory_summary_context"] = str(summary_context or "")' in short_source
    assert "build_active_history(" in short_source
    assert "record_message(" in short_source
    assert 'kwargs["aliyun_memory_context_ready"] = True' in long_source
    assert 'kwargs["aliyun_memory_context"] = memory_block' in long_source
    assert "plan_context(" in long_source
    assert "allowed_memory_types=plan.allowed_memory_types" in long_source


def test_main_chat_compiles_structured_context_and_attaches_a_manifest():
    source = (
        ROOT / "plugins" / "xiaoyou_chat" / "__init__.py"
    ).read_text(encoding="utf-8")

    assert "compile_context_pack(" in source
    assert 'kwargs["xiaoyou_context_pack_manifest"] = context_pack.manifest' in source
    assert "context_pack.rendered" in source
    assert 'version="1.7-continuity-recovery"' in source
    assert "build_chat_messages(" in source
    assert "SelectiveCritic" in source
    assert "get_recent_state_service" in source
    assert "build_episodic_context(" in source
    assert "_recover_with_continuity(" in source
    assert '"durable_history_preserved": True' in source
    assert 'episodic_memory=kwargs.get("xiaoyou_episodic_context", "")' in source
    silent_branch = source.split(
        "no reply sent because llm failed and preset fallback is disabled",
        1,
    )[1][:700]
    assert "e_context.action = EventAction.BREAK_PASS" in silent_branch


def test_compose_exposes_context_budget_controls():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    for key in (
        "XIAOYOU_CONTEXT_MAX_CHARS",
        "XIAOYOU_CONTEXT_CURRENT_MAX_CHARS",
        "XIAOYOU_CONTEXT_RECENT_STATE_MAX_CHARS",
        "XIAOYOU_CONTEXT_SHORT_MAX_CHARS",
        "XIAOYOU_CONTEXT_EPISODIC_MAX_CHARS",
        "XIAOYOU_CONTEXT_LONG_MAX_CHARS",
        "XIAOYOU_CONTEXT_UPSTREAM_MAX_CHARS",
        "XIAOYOU_MODEL_CONTEXT_TOKENS",
        "XIAOYOU_SYSTEM_RESERVED_TOKENS",
        "XIAOYOU_OUTPUT_RESERVED_TOKENS",
        "XIAOYOU_THINKING_RESERVED_TOKENS",
        "XIAOYOU_CONTEXT_MAX_TOKENS",
        "XIAOYOU_CONTEXT_CURRENT_MAX_TOKENS",
        "XIAOYOU_CONTEXT_RECENT_STATE_MAX_TOKENS",
        "XIAOYOU_CONTEXT_SHORT_MAX_TOKENS",
        "XIAOYOU_CONTEXT_EPISODIC_MAX_TOKENS",
        "XIAOYOU_CONTEXT_LONG_MAX_TOKENS",
        "XIAOYOU_CONTEXT_UPSTREAM_MAX_TOKENS",
        "XIAOYOU_NATIVE_HISTORY_MAX_MESSAGES",
        "XIAOYOU_NATIVE_HISTORY_MAX_TOKENS",
        "XIAOYOU_NATIVE_HISTORY_RESERVED_TOKENS",
        "XIAOYOU_CONTENT_RECOVERY_HISTORY_MESSAGES",
        "XIAOYOU_CONTENT_RECOVERY_MAX_ATTEMPTS",
        "XIAOYOU_RECENT_STATE_ENABLED",
        "XIAOYOU_SELECTIVE_CRITIC_ENABLED",
        "XIAOYOU_CONVERSATION_ARCHIVE_ENABLED",
        "XIAOYOU_ACTIVE_WINDOW_HOURS",
        "XIAOYOU_EPISODE_IDLE_SECONDS",
        "XIAOYOU_EPISODIC_MIN_SCORE",
    ):
        assert key + ":" in compose


def test_persona_stays_compact_and_does_not_reintroduce_fixed_reactions():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    persona = compose.split("CHARACTER_DESC: |", 1)[1].split(
        "# ===== 微信触发与回复前缀 =====",
        1,
    )[0]
    chat_source = (
        ROOT / "plugins" / "xiaoyou_chat" / "__init__.py"
    ).read_text(encoding="utf-8")

    assert "你有好奇心和思考能力" in persona
    assert "内容复杂时可以自然展开" in persona
    assert "不凭空补充地点、动作或已经发生的事情" in persona
    assert "制裁" not in persona
    assert "每一行尽量控制" not in persona
    assert "不要使用编号、标题、列表格式" not in persona
    assert "最近六个助手回合" not in chat_source
    assert "XIAOYOU_CHAT_MAX_TOKENS: '700'" in compose

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "plugins" / "long_memory" / "long_memory.py"
DISPATCHER_PATH = (
    ROOT / "plugins" / "xiaoyou_common" / "outbound_dispatcher.py"
)
CHAT_CHANNEL_PATH = ROOT / "patches" / "chat_channel.py"


def _method_source(source, name, next_name):
    start = source.index("    def %s(" % name)
    end = source.index("    def %s(" % next_name, start)
    return source[start:end]


def test_governance_writes_exact_user_fact_to_local_sqlite():
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    method = _method_source(source, "_write_governed_candidate", "_govern_memory_turn")

    assert "self.store.upsert(" in method
    assert '"provider_memory_id": memory_id' in method
    assert '"role": "assistant"' not in method
    assert "requests." not in method


def test_committed_user_text_is_queued_before_reply_generation():
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    method = _method_source(source, "on_handle_context", "_embed")

    assert "self._enqueue_memory_turn(" in method
    assert 'kwargs["long_memory_user_text"] = user_text' in method
    assert 'kwargs["long_memory_governance_enqueued"] = True' in method
    assert "ON_DECORATE_REPLY" not in source


def test_delivered_assistant_turn_is_queued_with_delivery_idempotency():
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    method = _method_source(
        source,
        "append_delivered_assistant_message",
        "_on_memory_job_error",
    )

    assert 'source_mode="assistant_delivered"' in method
    assert '"delivery:" + action_id' in method
    assert "assistant_text=assistant_text" in method
    assert "delivery_complete=bool(delivery_complete)" in method
    assert "terminal_status=str(terminal_status or" in method


def test_dispatcher_records_only_actual_sent_parts_after_terminal_delivery():
    source = DISPATCHER_PATH.read_text(encoding="utf-8")
    send_call = source.index("_record_delivered_long_memory(receipt, context=context)")
    send_result = source.index("receipt.sent_parts.append(part)")
    helper = source[source.index("def _record_delivered_long_memory("):]

    assert send_call > send_result
    assert "if not receipt.sent_text:" in helper
    assert "receipt.sent_text," in helper
    assert 'terminal_status = "stale_partial"' in helper
    assert "delivery_complete=receipt.ok" in helper


def test_direct_chat_send_records_long_memory_only_after_success():
    source = CHAT_CHANNEL_PATH.read_text(encoding="utf-8")
    send_result = source.index("_send_ok = self._send(reply, context)")
    delivery_record = source.index("record_delivered_assistant_long_memory(", send_result)
    success_guard = source.index("if (\n                    _send_ok", send_result)

    assert send_result < success_guard < delivery_record


def test_retrieval_is_embedding_based_without_keyword_or_regex_matching():
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    method = _method_source(
        source,
        "_search_memory",
        "_schedule_embedding_backfill",
    )

    assert 'purpose="semantic_query"' in method
    assert "self._cosine_similarity(" in method
    assert "embedding_signature" in method
    assert "re.search(" not in method
    assert "near_duplicate_text" not in source


def test_legacy_vector_backfill_runs_outside_the_chat_path():
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    search = _method_source(
        source,
        "_search_memory",
        "_schedule_embedding_backfill",
    )
    schedule = _method_source(
        source,
        "_schedule_embedding_backfill",
        "_run_embedding_backfill",
    )

    assert "self._schedule_embedding_backfill()" in search
    assert "threading.Thread(" in schedule
    assert "daemon=True" in schedule
    assert "self._run_embedding_backfill()" not in search

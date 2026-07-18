from pathlib import Path
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyou_runtime_log_analyzer_under_test",
    ROOT / "evals" / "analyze_runtime_log.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_runtime_log_metrics_are_derived_per_trace():
    log = """
[INFO][2026-07-17 20:00:00][trace_service.py:1] - [Trace] stage=input_received status=accepted trace_id=t1 input_id=i1 session=yoyo model_call_id=- lease_id=- action_id=- memory_record_id=-
[INFO][2026-07-17 20:00:01][model_gateway.py:1] - [ModelGateway] completed component=XiaoyouChat purpose=XIAOYOU_CHAT call_id=c1 model=qwen session=yoyo elapsed=1.50s has_content=True thinking_fallback=False
[INFO][2026-07-17 20:00:01][xiaoyou_chat.py:1] - [XiaoyouChat] context pack compiled chars=1200/7000 tokens=800/4000 plan=emotional sections={} structured=True
[INFO][2026-07-17 20:00:01][xiaoyou_chat.py:1] - [XiaoyouChat] native history prepared messages=6
[INFO][2026-07-17 20:00:01][xiaoyou_chat.py:1] - [XiaoyouChat] selective critic status=skipped risks=0
[INFO][2026-07-17 20:00:01][aliyun_memory.py:1] - [AliyunMemory] retrieval skipped plan=emotional reason=current
[INFO][2026-07-17 20:00:02][trace_service.py:1] - [Trace] stage=outbound_started status=started trace_id=t1 input_id=i1 session=yoyo model_call_id=- lease_id=- action_id=a1 memory_record_id=- requested_parts=2
[INFO][2026-07-17 20:00:03][trace_service.py:1] - [Trace] stage=outbound_completed status=ok trace_id=t1 input_id=i1 session=yoyo model_call_id=- lease_id=- action_id=a1 memory_record_id=m1 sent_parts=2 stale=False
[INFO][2026-07-17 20:00:04][aliyun_memory.py:1] - [AliyunMemory] governance completed session=yoyo extracted=2 eligible=1 written=1 failed=0
[INFO][2026-07-17 20:00:04][recent_state_service.py:1] - [RecentState] updated session=yoyo topic=True states=1 loops=0 refs=0 facts=1
[INFO][2026-07-17 20:00:04][conversation_archive_service.py:1] - [ConversationArchive] message archived session=yoyo role=user episode=e1
[INFO][2026-07-17 20:00:04][conversation_archive_service.py:1] - [ConversationArchive] message archived session=yoyo role=assistant episode=e1
[INFO][2026-07-17 20:00:05][conversation_archive_service.py:1] - [EpisodeBuilder] episode ready id=e0
[INFO][2026-07-17 20:00:05][conversation_archive_service.py:1] - [EpisodicMemory] retrieved episodes=2 mode=project
[INFO][2026-07-17 20:00:05][conversation_archive_service.py:1] - [ConversationArchive] backup completed generations=3
"""
    report = MODULE.analyze_text(log)

    assert report["turns"]["input_traces"] == 1
    assert report["turns"]["completed"] == 1
    assert report["latency"]["input_to_outbound_start"]["median_seconds"] == 2.0
    assert report["latency"]["input_to_all_sent"]["median_seconds"] == 3.0
    assert report["delivery"]["mean_bubbles"] == 2
    assert report["context"]["plans"] == {"emotional": 1}
    assert report["context"]["memory_plans"] == {"emotional:skipped": 1}
    assert report["memory_governance"]["written"] == 1
    assert report["context"]["mean_native_history_messages"] == 6
    assert report["conversation_control"]["critic_statuses"] == {"skipped": 1}
    assert report["conversation_control"]["recent_state_statuses"] == {"updated": 1}
    assert report["context"]["mean_episodes_retrieved"] == 2
    assert report["context"]["episodic_modes"] == {"project": 1}
    assert report["conversation_archive"] == {
        "messages_archived": 2,
        "episodes_ready": 1,
        "backups_completed": 1,
    }

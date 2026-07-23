import importlib.util
import threading
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "xiaoyou_common"
    / "session_fifo.py"
)
SPEC = importlib.util.spec_from_file_location("session_fifo_under_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PerSessionFIFO = MODULE.PerSessionFIFO


def test_jobs_for_one_session_run_in_submission_order():
    first_started = threading.Event()
    release_first = threading.Event()
    handled = []

    def handle(session_id, sequence, payload):
        if payload == "first":
            first_started.set()
            assert release_first.wait(1)
        handled.append((session_id, sequence, payload))

    queue = PerSessionFIFO(handle)
    assert queue.submit("yoyo", "first") == 1
    assert first_started.wait(1)
    assert queue.submit("yoyo", "second") == 2
    assert not queue.wait_idle("yoyo", timeout=0.02)

    release_first.set()
    assert queue.wait_idle("yoyo", timeout=1)
    assert handled == [
        ("yoyo", 1, "first"),
        ("yoyo", 2, "second"),
    ]


def test_different_sessions_can_run_concurrently():
    release_yoyo = threading.Event()
    other_finished = threading.Event()

    def handle(session_id, sequence, payload):
        del sequence, payload
        if session_id == "yoyo":
            assert release_yoyo.wait(1)
        else:
            other_finished.set()

    queue = PerSessionFIFO(handle)
    queue.submit("yoyo", "blocked")
    queue.submit("other", "independent")

    assert other_finished.wait(1)
    release_yoyo.set()
    assert queue.wait_idle(timeout=1)


def test_stale_sequence_is_rejected_and_one_error_does_not_stop_the_queue():
    errors = []
    handled = []

    def handle(session_id, sequence, payload):
        if payload == "bad":
            raise RuntimeError("boom")
        handled.append((session_id, sequence, payload))

    queue = PerSessionFIFO(
        handle,
        on_error=lambda session, sequence, payload, error: errors.append(
            (session, sequence, payload, type(error).__name__)
        ),
    )
    assert queue.submit("yoyo", "bad", sequence=5) == 5
    assert queue.submit("yoyo", "good") == 6
    assert queue.wait_idle("yoyo", timeout=1)

    assert queue.submit("yoyo", "stale", sequence=6) == 0
    assert errors == [("yoyo", 5, "bad", "RuntimeError")]
    assert handled == [("yoyo", 6, "good")]

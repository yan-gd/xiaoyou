# -*- coding: utf-8 -*-
"""Small per-session FIFO executor for non-blocking background work."""

import threading
import time
from collections import deque


class PerSessionFIFO:
    """Run jobs serially within one session and concurrently across sessions."""

    def __init__(self, handler, *, on_error=None, thread_name_prefix="session-fifo"):
        if not callable(handler):
            raise ValueError("handler must be callable")
        self.handler = handler
        self.on_error = on_error
        self.thread_name_prefix = str(thread_name_prefix or "session-fifo")
        self._condition = threading.Condition(threading.RLock())
        self._queues = {}
        self._running = set()
        self._submitted = {}
        self._completed = {}

    def submit(self, session_id, payload, *, sequence=None):
        """Queue one job and return its session-local sequence.

        An explicitly supplied sequence must be newer than every previously
        submitted sequence for that session. Returning ``0`` means the job
        was rejected as stale or duplicate.
        """
        session_key = str(session_id or "_default")
        with self._condition:
            latest = max(
                int(self._submitted.get(session_key, 0)),
                int(self._completed.get(session_key, 0)),
            )
            if sequence is None:
                job_sequence = latest + 1
            else:
                try:
                    job_sequence = int(sequence)
                except (TypeError, ValueError):
                    return 0
                if job_sequence <= latest:
                    return 0

            queue = self._queues.setdefault(session_key, deque())
            queue.append((job_sequence, payload))
            self._submitted[session_key] = job_sequence
            if session_key not in self._running:
                self._running.add(session_key)
                worker = threading.Thread(
                    target=self._drain,
                    args=(session_key,),
                    name="%s-%s"
                    % (
                        self.thread_name_prefix,
                        self._safe_thread_suffix(session_key),
                    ),
                    daemon=True,
                )
                worker.start()
            self._condition.notify_all()
            return job_sequence

    def completed_sequence(self, session_id):
        with self._condition:
            return int(self._completed.get(str(session_id or "_default"), 0))

    def wait_idle(self, session_id=None, timeout=None):
        """Wait until a session (or every session) has no queued/running job."""
        deadline = (
            None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        )
        session_key = None if session_id is None else str(session_id or "_default")
        with self._condition:
            while not self._is_idle(session_key):
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def _drain(self, session_key):
        while True:
            with self._condition:
                queue = self._queues.get(session_key)
                if not queue:
                    self._queues.pop(session_key, None)
                    self._running.discard(session_key)
                    self._condition.notify_all()
                    return
                sequence, payload = queue.popleft()

            try:
                self.handler(session_key, sequence, payload)
            except Exception as exc:
                if callable(self.on_error):
                    try:
                        self.on_error(session_key, sequence, payload, exc)
                    except Exception:
                        pass
            finally:
                with self._condition:
                    self._completed[session_key] = max(
                        int(self._completed.get(session_key, 0)),
                        int(sequence),
                    )
                    self._condition.notify_all()

    def _is_idle(self, session_key):
        if session_key is None:
            return not self._running and not any(self._queues.values())
        return session_key not in self._running and not self._queues.get(session_key)

    @staticmethod
    def _safe_thread_suffix(session_key):
        suffix = "".join(
            char if char.isalnum() or char in ("-", "_") else "_"
            for char in session_key
        )
        return suffix[:32] or "default"

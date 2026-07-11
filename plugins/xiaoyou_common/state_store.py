# -*- coding: utf-8 -*-
"""Reliable JSON state storage shared by Xiaoyou capabilities."""

import copy
import json
import os
import threading
import uuid

from common.log import logger


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS = {}


class JsonStateStore:
    """Atomic primary/backup JSON storage with last-good protection.

    A missing state file is a normal first-run condition and returns the
    configured default.  If state files exist but neither can be decoded or
    validated, writes are blocked so a caller's empty fallback cannot erase
    recoverable user data.
    """

    def __init__(
        self,
        path,
        *,
        backup_path=None,
        name="state",
        default_factory=dict,
        expected_type=dict,
        strict_unavailable=False,
    ):
        self.path = os.path.abspath(os.fspath(path))
        self.backup_path = os.path.abspath(
            os.fspath(backup_path or (self.path + ".backup"))
        )
        self.name = str(name or "state")
        self.default_factory = default_factory if callable(default_factory) else dict
        self.expected_type = expected_type
        self.strict_unavailable = bool(strict_unavailable)
        self.lock = _path_lock(self.path)
        self.last_good = None
        self.write_blocked = False
        self.last_load_source = ""

    def load(self, transform=None):
        with self.lock:
            existing = False
            errors = []

            for source, path in (
                ("primary", self.path),
                ("backup", self.backup_path),
            ):
                if not os.path.exists(path):
                    continue
                existing = True
                try:
                    data = self._load_path(path)
                    if callable(transform):
                        data = transform(data)
                        self._validate(data)
                    self.last_good = copy.deepcopy(data)
                    self.write_blocked = False
                    self.last_load_source = source
                    if source == "backup":
                        logger.warning(
                            "[StateStore] recovered name=%s source=backup primary=%s",
                            self.name,
                            self.path,
                        )
                    return copy.deepcopy(data)
                except Exception as exc:
                    errors.append((source, type(exc).__name__, str(exc)[:160]))
                    logger.error(
                        "[StateStore] load failed name=%s source=%s path=%s error=%s",
                        self.name,
                        source,
                        path,
                        type(exc).__name__,
                    )

            if not existing:
                data = self._default_value()
                if callable(transform):
                    data = transform(data)
                    self._validate(data)
                self.last_good = copy.deepcopy(data)
                self.write_blocked = False
                self.last_load_source = "default"
                return data

            if self.last_good is not None:
                self.write_blocked = False
                self.last_load_source = "memory"
                logger.warning(
                    "[StateStore] using in-memory last-good name=%s",
                    self.name,
                )
                return copy.deepcopy(self.last_good)

            self.write_blocked = True
            self.last_load_source = "unavailable"
            logger.error(
                "[StateStore] no valid state name=%s writes_blocked=true errors=%s",
                self.name,
                errors,
            )
            if self.strict_unavailable:
                return None
            return self._default_value()

    def save(self, data):
        with self.lock:
            if self.write_blocked and self.last_good is None:
                logger.error(
                    "[StateStore] save refused name=%s reason=invalid_primary_and_backup",
                    self.name,
                )
                return False

            try:
                self._validate(data)
                payload = json.dumps(data, ensure_ascii=False, indent=2)
            except Exception:
                logger.exception(
                    "[StateStore] serialization refused name=%s",
                    self.name,
                )
                return False

            if not self._atomic_write(self.path, payload):
                return False

            self.last_good = copy.deepcopy(data)
            self.write_blocked = False
            self.last_load_source = "primary"

            if not self._atomic_write(self.backup_path, payload, backup=True):
                # A committed primary remains valid even if the redundant copy
                # cannot be refreshed during this operation.
                logger.warning(
                    "[StateStore] backup refresh failed name=%s primary_kept=true",
                    self.name,
                )
            return True

    def remember(self, data):
        """Mark a caller-validated/migrated value as the in-memory last good."""
        with self.lock:
            self._validate(data)
            self.last_good = copy.deepcopy(data)
            self.write_blocked = False
            self.last_load_source = "memory"
            return copy.deepcopy(data)

    def _load_path(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self._validate(data)
        return data

    def _validate(self, data):
        expected = self.expected_type
        if expected is not None and not isinstance(data, expected):
            name = getattr(expected, "__name__", str(expected))
            raise ValueError("state root must be %s" % name)

    def _default_value(self):
        data = self.default_factory()
        self._validate(data)
        return copy.deepcopy(data)

    def _atomic_write(self, path, payload, backup=False):
        directory = os.path.dirname(path) or "."
        temp_path = "%s.tmp.%s" % (path, uuid.uuid4().hex)
        try:
            os.makedirs(directory, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            return True
        except Exception:
            logger.exception(
                "[StateStore] %s save failed name=%s path=%s",
                "backup" if backup else "primary",
                self.name,
                path,
            )
            return False
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                logger.warning(
                    "[StateStore] temporary file cleanup failed name=%s path=%s",
                    self.name,
                    temp_path,
                )


def _path_lock(path):
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock

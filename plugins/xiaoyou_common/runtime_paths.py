# -*- coding: utf-8 -*-
"""Canonical paths for mutable Xiaoyou runtime data.

Plugin directories are deployable source code and must not remain the
authoritative location for memories, schedules, or other mutable state.  This
module resolves every such file below ``APPDATA_DIR`` (``/app/data`` in the
container) and performs a conservative one-time copy from legacy locations.
"""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path

from common.log import logger


_MIGRATION_LOCK = threading.RLock()


def appdata_root():
    """Return the persistent runtime-data root.

    Production configures ``APPDATA_DIR=/app/data``.  Local development falls
    back to the repository's ignored ``data`` directory instead of ``plugins``.
    """

    configured = os.getenv("APPDATA_DIR", "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    repository_root = Path(__file__).resolve().parents[2]
    return str(repository_root / "data")


def runtime_path(
    namespace,
    filename,
    *,
    env_var="",
    legacy_paths=(),
    migrate_backup=True,
):
    """Resolve a mutable file below APPDATA_DIR and copy legacy state once.

    Absolute environment overrides are honored.  Relative overrides remain
    below APPDATA_DIR.  Existing data-directory state is never overwritten by
    an older plugin-local copy.  Legacy files are retained as rollback evidence
    while all subsequent reads and writes use the returned destination.
    """

    namespace = str(namespace or "").strip().strip("/\\")
    filename = str(filename or "").strip().lstrip("/\\")
    if not filename:
        raise ValueError("runtime filename is required")

    configured = os.getenv(env_var, "").strip() if env_var else ""
    if configured:
        configured = os.path.expanduser(configured)
        target = (
            configured
            if os.path.isabs(configured)
            else os.path.join(appdata_root(), configured)
        )
    elif namespace:
        target = os.path.join(appdata_root(), namespace, filename)
    else:
        target = os.path.join(appdata_root(), filename)

    target = os.path.abspath(target)
    _migrate_legacy_state(
        target,
        legacy_paths=legacy_paths,
        migrate_backup=migrate_backup,
    )
    return target


def _migrate_legacy_state(target, *, legacy_paths, migrate_backup):
    target = os.path.abspath(os.fspath(target))
    target_backup = target + ".backup"

    with _MIGRATION_LOCK:
        if os.path.exists(target) or os.path.exists(target_backup):
            return

        for raw_path in legacy_paths:
            if raw_path is None:
                continue
            source = os.path.abspath(os.path.expanduser(os.fspath(raw_path)))
            source_backup = source + ".backup"
            if source == target:
                continue
            if not os.path.isfile(source) and not (
                migrate_backup and os.path.isfile(source_backup)
            ):
                continue

            migrated = []
            if os.path.isfile(source):
                _atomic_copy(source, target)
                migrated.append((source, target))
            if migrate_backup and os.path.isfile(source_backup):
                _atomic_copy(source_backup, target_backup)
                migrated.append((source_backup, target_backup))

            if migrated:
                logger.warning(
                    "[RuntimePaths] migrated mutable state namespace=%s files=%s",
                    os.path.basename(os.path.dirname(target)) or "data",
                    ["%s->%s" % (src, dst) for src, dst in migrated],
                )
            return


def _atomic_copy(source, target):
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    temporary = "%s.migrate.%s.tmp" % (target, uuid.uuid4().hex)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass


__all__ = ["appdata_root", "runtime_path"]

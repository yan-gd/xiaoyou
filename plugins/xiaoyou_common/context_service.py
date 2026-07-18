# -*- coding:utf-8 -*-
"""Read-only conversation context facade shared by Xiaoyou plugins.

This module owns context acquisition and compatibility handling.  Callers still
decide which facts belong in a prompt and how the model should use them.
"""

import os
from dataclasses import dataclass

from common.log import logger
from plugins.xiaoyou_common.message_context import extract_current_user_text as _extract_current_user_text
from plugins.xiaoyou_common.time_context import build_time_context as _build_time_context


@dataclass(frozen=True)
class ContextSnapshot:
    current_user_text: str = ""
    time_context: str = ""
    character_context: str = ""
    long_memory: str = ""
    short_memory: str = ""


class ContextService:
    """Resolve context providers without exposing plugin-manager details."""

    def __init__(self, instances_provider=None):
        self._instances_provider = instances_provider

    def extract_current_user_text(self, content):
        return _extract_current_user_text(content)

    def build_time_context(self):
        return _build_time_context()

    def build_character_context(self, character_desc=None, include_time=True):
        character = (
            os.getenv("CHARACTER_DESC", "")
            if character_desc is None
            else str(character_desc or "")
        ).strip()
        if not include_time:
            return character

        time_context = self.build_time_context()
        if time_context and time_context not in character:
            character = (character + "\n\n" + time_context).strip()
        return character

    def load_long_memory(
        self,
        query,
        max_results=None,
        retrieval_mode="normal",
        allowed_memory_types=None,
        component="unknown",
    ):
        query = str(query or "").strip()
        if not query:
            return ""

        try:
            memory = self._instances().get("ALIYUNMEMORY")
            build_context = getattr(memory, "build_memory_context", None)
            if not callable(build_context):
                logger.info(
                    "[ContextService] long-memory provider unavailable component=%s",
                    component,
                )
                return ""

            args = (query,) if max_results is None else (query, max(0, int(max_results)))
            try:
                value = build_context(
                    *args,
                    retrieval_mode=retrieval_mode,
                    allowed_memory_types=allowed_memory_types,
                )
            except TypeError:
                try:
                    # Compatibility with providers predating memory types.
                    value = build_context(*args, retrieval_mode=retrieval_mode)
                except TypeError:
                    # Compatibility with providers predating retrieval_mode.
                    value = build_context(*args)
            return str(value or "").strip()
        except Exception:
            logger.exception(
                "[ContextService] long-memory lookup failed component=%s",
                component,
            )
            return ""

    def load_short_memory(self, session_id, max_chars=None, component="unknown"):
        session_id = str(session_id or "").strip()
        if not session_id:
            return ""

        try:
            memory = self._instances().get("SHORTMEMORY")
            build_context = getattr(memory, "build_context_for_external_consumer", None)
            if not callable(build_context):
                logger.info(
                    "[ContextService] short-memory provider unavailable component=%s",
                    component,
                )
                return ""

            value = str(build_context(session_id) or "").strip()
            if max_chars is None:
                return value
            limit = int(max_chars)
            return value[-limit:] if limit > 0 else value
        except Exception:
            logger.exception(
                "[ContextService] short-memory lookup failed component=%s",
                component,
            )
            return ""

    def snapshot(
        self,
        *,
        content="",
        session_id="",
        long_memory_query="",
        long_memory_max_results=None,
        retrieval_mode="normal",
        allowed_memory_types=None,
        include_time=True,
        include_character=True,
        include_short_memory=False,
        short_memory_max_chars=None,
        component="unknown",
    ):
        time_context = self.build_time_context() if include_time else ""
        character_context = ""
        if include_character:
            character_context = self.build_character_context(
                include_time=False,
            )
            if time_context and time_context not in character_context:
                character_context = (
                    character_context + "\n\n" + time_context
                ).strip()

        long_memory = ""
        if str(long_memory_query or "").strip():
            long_memory = self.load_long_memory(
                long_memory_query,
                max_results=long_memory_max_results,
                retrieval_mode=retrieval_mode,
                allowed_memory_types=allowed_memory_types,
                component=component,
            )

        short_memory = ""
        if include_short_memory:
            short_memory = self.load_short_memory(
                session_id,
                max_chars=short_memory_max_chars,
                component=component,
            )

        return ContextSnapshot(
            current_user_text=self.extract_current_user_text(content),
            time_context=time_context,
            character_context=character_context,
            long_memory=long_memory,
            short_memory=short_memory,
        )

    def _instances(self):
        if self._instances_provider is not None:
            instances = self._instances_provider()
            return instances if isinstance(instances, dict) else {}

        import plugins

        manager = getattr(plugins, "instance", None)
        instances = getattr(manager, "instances", {}) if manager else {}
        return instances if isinstance(instances, dict) else {}


context_service = ContextService()


def extract_current_user_text(content):
    return context_service.extract_current_user_text(content)


def build_time_context():
    return context_service.build_time_context()


def build_character_context(character_desc=None, include_time=True):
    return context_service.build_character_context(character_desc, include_time)


def load_long_memory_context(
    query,
    max_results=None,
    retrieval_mode="normal",
    allowed_memory_types=None,
    component="unknown",
):
    return context_service.load_long_memory(
        query,
        max_results=max_results,
        retrieval_mode=retrieval_mode,
        allowed_memory_types=allowed_memory_types,
        component=component,
    )


def load_short_memory_context(session_id, max_chars=None, component="unknown"):
    return context_service.load_short_memory(
        session_id,
        max_chars=max_chars,
        component=component,
    )


def build_context_snapshot(**kwargs):
    return context_service.snapshot(**kwargs)

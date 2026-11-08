"""Narrow owner/host binding; Services retains bot credentials and HTTP.

The host composes ``DiscordAttentionBinding(lambda authorize:
DiscordBotClient('knowledgeos', private_bot, private_channels, authorize))``.
Nothing serializes client bindings, tokens, headers, connections or exceptions.
The Services clients package is a host dependency, not an owner dependency.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from ..application.owner_attention import AttentionNotice, AttentionOutcome
from .core import AdmissionError


class DiscordAttentionBinding:
    def __init__(self, host_factory: Callable):
        from clients.discord import DiscordBotClient

        self._lock = threading.Lock()
        self._active: tuple[AttentionNotice, Callable] | None = None
        self._client = host_factory(self._authorize)
        if not isinstance(self._client, DiscordBotClient) or self._client.owner_operation_id != "knowledgeos":
            raise AdmissionError("ATTENTION_DENIED", "Host binding must supply the KnowledgeOS Services bot client")

    def _authorize(self, bot_notice):
        from clients.discord import BotNotice

        if self._active is None:
            raise AdmissionError("ATTENTION_DENIED", "No current owner dispatch")
        notice, authorize = self._active
        expected = BotNotice("knowledgeos", notice.logical_destination_id,
                             notice.destination_generation, notice.notice_id,
                             notice.content, notice.expires_at)
        if bot_notice != expected or notice.expires_at <= datetime.now(UTC):
            raise AdmissionError("ATTENTION_DENIED", "Host notice differs from the admitted export")
        authorize(notice)
        return bot_notice

    def send_once(self, notice: AttentionNotice,
                  authorize: Callable[[AttentionNotice], AttentionNotice]) -> AttentionOutcome:
        from clients.discord import BotNotice

        with self._lock:
            self._active = (notice, authorize)
            try:
                outcome = self._client.send_once(BotNotice(
                    "knowledgeos", notice.logical_destination_id, notice.destination_generation,
                    notice.notice_id, notice.content, notice.expires_at))
                return AttentionOutcome(outcome.state, outcome.message_id, outcome.retry_after_seconds)
            finally:
                self._active = None

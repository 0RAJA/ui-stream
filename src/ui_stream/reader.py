# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""Ordered Redis live iteration with optional host-owned state reconciliation.

The host supplies authorization and execution-state checks without exposing its
models to the reader. Durable terminal hints drain the current tail; reset or
state-check errors stop queued delivery immediately.
"""

import logging
import math
import re
import time

from ui_stream.codec import ControlEvent
from ui_stream.exceptions import InvalidCursor, LiveUnavailable

logger = logging.getLogger(__name__)
STREAM_CURSOR_PATTERN = re.compile(r"^[0-9]+-[0-9]+$")
STREAM_ID_PART_MAX = str(2**64 - 1)
STREAM_START_CURSOR = "0-0"
CLOSING_EVENTS = frozenset((ControlEvent.STREAM_RESET, ControlEvent.STREAM_END))


def normalize_cursor(value):
    if not value:
        return STREAM_START_CURSOR
    if not isinstance(value, str) or not STREAM_CURSOR_PATTERN.fullmatch(value):
        raise InvalidCursor("Invalid stream cursor")
    for part in value.split("-"):
        digits = part.lstrip("0") or "0"
        if len(digits) > len(STREAM_ID_PART_MAX) or (
            len(digits) == len(STREAM_ID_PART_MAX) and digits > STREAM_ID_PART_MAX
        ):
            raise InvalidCursor("Invalid stream cursor")
    return value


def iter_live(
    *,
    redis_store,
    redis_key,
    cursor,
    block_ms=15000,
    idle_timeout=300,
    clock=time.monotonic,
    live_errors=(LiveUnavailable, OSError),
    state_check=None,
    state_poll_interval=15,
):
    """Yield ordered live events and heartbeats, optionally reconciling host state.

    ``state_check`` returns a closing event or None and may raise host-owned
    exceptions. A durable end is held until the raw Redis tail is empty, while
    any other state event closes immediately. Checks continue during draining.
    """

    if state_check is not None and state_poll_interval <= 0:
        raise ValueError("state_poll_interval must be positive")
    now = clock()
    last_business_event_at = now
    next_state_check = now + state_poll_interval if state_check is not None else None
    terminal_event = None

    def refresh_state(now):
        nonlocal next_state_check, terminal_event
        if now < next_state_check:
            return None
        state_event = state_check()
        next_state_check = now + state_poll_interval
        if state_event is not None and state_event.event == ControlEvent.STREAM_END:
            terminal_event = state_event
            return None
        return state_event

    while True:
        read_block_ms = block_ms
        if state_check is not None:
            now = clock()
            state_event = refresh_state(now)
            if state_event is not None:
                yield state_event
                return
            # A socket read shares the next check's deadline, even on a busy stream.
            read_block_ms = (
                0 if terminal_event is not None else min(block_ms, max(1, math.ceil((next_state_check - now) * 1000)))
            )
        try:
            read_result = redis_store.read(
                redis_key=redis_key,
                after_id=cursor,
                block_ms=read_block_ms,
            )
        except live_errors:
            # Redis 故障只终止本次 SSE；前端会重新读取详情和快照。
            logger.warning(
                "UI stream live read unavailable",
                extra={"redis_key": redis_key},
            )
            return
        if state_check is None:
            # Preserve one clock read per batch for consumers without reconciliation.
            now = clock()

        # Dirty entries still advance the raw cursor, preventing repeated reads.
        if read_result.last_seen_stream_id:
            cursor = read_result.last_seen_stream_id

        has_business_event = False
        for event in read_result.events or (None,):
            if state_check is not None:
                now = clock()
                state_event = refresh_state(now)
                if state_event is not None:
                    yield state_event
                    return
            if event is None:
                if terminal_event is not None:
                    # A dirty-only batch has not exhausted the raw tail.
                    if read_result.last_seen_stream_id:
                        break
                    yield terminal_event
                    return
                if now - last_business_event_at >= idle_timeout:
                    return
                yield None
                continue
            yield event
            if event.event in CLOSING_EVENTS:
                # Preserve the original closing event and its Redis cursor.
                return
            if event.event is None:
                has_business_event = True
                last_business_event_at = now

        # Heartbeats and control/dirty batches never refresh business activity.
        if terminal_event is None and not has_business_event and now - last_business_event_at >= idle_timeout:
            return

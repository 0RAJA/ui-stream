# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""Redis-only live iteration; authorization and initial state checks belong to the host."""

import logging
import re
import time

from ui_stream.codec import ControlEvent
from ui_stream.exceptions import InvalidCursor, LiveUnavailable

logger = logging.getLogger(__name__)
STREAM_CURSOR_PATTERN = re.compile(r"^\d+-\d+$")
STREAM_START_CURSOR = "0-0"
CLOSING_EVENTS = frozenset((ControlEvent.STREAM_RESET, ControlEvent.STREAM_END))


def normalize_cursor(value):
    if not value:
        return STREAM_START_CURSOR
    if not STREAM_CURSOR_PATTERN.fullmatch(value):
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
    live_errors=(LiveUnavailable, OSError)
):
    last_business_event_at = clock()
    while True:
        try:
            read_result = redis_store.read(
                redis_key=redis_key,
                after_id=cursor,
                block_ms=block_ms,
            )
        except live_errors:
            # Redis 故障只终止本次 SSE；前端会重新读取详情和快照。
            logger.warning(
                "UI stream live read unavailable",
                extra={"redis_key": redis_key},
            )
            return
        # 每轮只取一次时钟，避免测试及超时边界因分支额外调用而漂移。
        now = clock()

        # XREAD 可能只读到被丢弃的脏 entry；即使无有效事件也要
        # 推进游标，否则下一轮会立即重读同一批数据。
        if read_result.last_seen_stream_id:
            cursor = read_result.last_seen_stream_id

        has_business_event = False
        for event in read_result.events:
            yield event
            if event.event in CLOSING_EVENTS:
                # 终止事件已下发，后续 entry 属于旧流残留，不再推送。
                return
            if event.event is None:
                # Event.event 为空表示默认 SSE message，即业务事件；
                # heartbeat 是迭代器返回的 Python None，不会进入此分支。
                has_business_event = True
                last_business_event_at = now

        # heartbeat、脏 entry 和平台事件都不刷新窗口；平台事件批次也必须超时。
        if not has_business_event and now - last_business_event_at >= idle_timeout:
            return
        if not read_result.events:
            yield None

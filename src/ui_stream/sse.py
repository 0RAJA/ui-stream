# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""Native SSE encoding; synthetic controls omit IDs and heartbeat is a comment."""

from ui_stream.codec import Event, serialize_data
from ui_stream.reader import normalize_cursor

# SSE 注释行；只用于保持连接活跃，客户端 EventSource 会自动忽略。
SSE_HEARTBEAT = b": heartbeat\n\n"


def encode_sse_event(event: Event) -> bytes:
    """按可选 event、可选 id、必选 data 的顺序编码原生 SSE 帧。"""

    if event.stream_id is not None:
        normalize_cursor(event.stream_id)
    payload = serialize_data(event.data).decode("utf-8")
    event_line = f"event: {event.event}\n" if event.event else ""
    # 只有来自 Redis 的事件才有游标；合成事件不写 id，避免客户端错误续传。
    id_line = f"id: {event.stream_id}\n" if event.stream_id else ""
    return f"{event_line}{id_line}data: {payload}\n\n".encode("utf-8")


def encode_sse_heartbeat() -> bytes:
    """返回心跳帧，用于在无新事件时探测连接并阻止中间层超时断开。"""

    return SSE_HEARTBEAT

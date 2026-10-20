# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""将默认 Event 编码为原生 SSE 帧，不负责网络响应或连接生命周期。

reader 的有效事件交给 encode_sse_event，None 由宿主转成心跳注释帧。
合成控制事件应由宿主留空 stream_id，避免改变客户端续传游标。
终态选择、归档回放及断连恢复仍由宿主和客户端协作处理。
"""

from ui_stream.codec import Event, serialize_data
from ui_stream.reader import normalize_cursor

# SSE 注释行；只用于保持连接活跃，客户端 EventSource 会自动忽略。
SSE_HEARTBEAT = b": heartbeat\n\n"


def encode_sse_event(event: Event) -> bytes:
    """按可选 event、可选 id、必选 data 的顺序输出一个完整 UTF-8 SSE 帧。

    非空 stream_id 先按 Redis 游标规则校验；是否写 id 仅由字段决定，不推断事件来源。
    data 使用严格 JSON，换行由 JSON 转义；此函数只返回字节，不发送或刷新响应。
    """

    if event.stream_id is not None:
        normalize_cursor(event.stream_id)
    payload = serialize_data(event.data).decode("utf-8")
    event_line = f"event: {event.event}\n" if event.event else ""
    # 合成事件应由宿主留空游标；这里按字段输出，不猜测或生成可续传的 ID。
    id_line = f"id: {event.stream_id}\n" if event.stream_id else ""
    return f"{event_line}{id_line}data: {payload}\n\n".encode("utf-8")


def encode_sse_heartbeat() -> bytes:
    """返回无 id/data 的 SSE 注释帧；只保活，不推进游标或表示业务活动。"""

    return SSE_HEARTBEAT

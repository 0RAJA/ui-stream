# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""提供同步 SSE 响应体与默认 Event 帧编码，保持标准库依赖。

SSEBody 将一次读取映射为一次编码，并接管这个读迭代器的 close 生命周期。
默认编码适配 JSON Event/None；自定义 profile 可注入 encoder，不必导入 Pydantic。
HTTP 响应、flush、共享客户端和异步/阻塞取消仍由宿主负责。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, Generic

from ui_stream._cursor import normalize_cursor
from ui_stream._json import serialize_data
from ui_stream.types import EventT

if TYPE_CHECKING:
    from ui_stream.codec import Event

logger = logging.getLogger(__name__)

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


def _encode_sse_item(event: Any) -> bytes:
    return encode_sse_heartbeat() if event is None else encode_sse_event(event)


class SSEBody(Iterator[bytes], Generic[EventT]):
    """把一个同步读迭代器编码为字节，并最多透传一次 close。

    仅接管传入 iterator；Iterable 的外层 owner、共享客户端和生产者不在其生命周期内。
    构造/iter 不读取数据，首次 next 前 close 也会透传。源本身如何释放未启动资源由源保证。
    调用须串行；close 不承诺打断正在阻塞的 next，不提供 async 适配或预取缓冲。
    """

    def __init__(self, events: Iterator[EventT], *, encoder: Callable[[EventT], bytes] = _encode_sse_item):
        """接管 events；encoder 每次接收一个原始 item，含自定义心跳占位值。"""

        if not isinstance(events, Iterator):
            raise TypeError("SSEBody requires a synchronous iterator; pass iter(events)")
        self._events = events
        self._encoder = encoder
        self._closed = False

    def __iter__(self) -> SSEBody[EventT]:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        try:
            event = next(self._events)
        except StopIteration:
            try:
                self.close()
            except StopIteration as error:
                # 清理失败不能被消费者误当作源的正常 EOF。
                raise RuntimeError("SSE source close must not raise StopIteration after exhaustion") from error
            raise
        except BaseException:
            self._close_preserving_error()
            raise
        try:
            frame = self._encoder(event)
            if not isinstance(frame, bytes):
                raise TypeError("SSE encoder must return bytes")
            return frame
        except StopIteration as error:
            self._close_preserving_error()
            raise RuntimeError("SSE encoder must not raise StopIteration") from error
        except BaseException:
            self._close_preserving_error()
            raise

    def close(self) -> None:
        """先关闭自身再透传给源；直接清理失败会传播，但不会重复调用源 close。"""

        if self._closed:
            return
        self._closed = True
        close = getattr(self._events, "close", None)
        if close is not None:
            close()

    def _close_preserving_error(self) -> None:
        """保留当前读/编码异常；普通清理错误只记类型，不泄漏源或异常正文。"""

        try:
            self.close()
        except Exception as error:
            logger.warning("SSE source cleanup failed", extra={"error_type": type(error).__name__})

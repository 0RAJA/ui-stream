# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""Redis live tail with injected connection/key mapping and atomic sliding TTL.

No consumer groups, ORM or current pointer. Backend/parser errors become
LiveUnavailable. Raw-byte dirty entries advance the real read cursor; predecoded
parser failures end the read because no entry IDs are available.
"""

import logging
from typing import Any

from pydantic import ValidationError
from redis.exceptions import RedisError

from ui_stream.codec import Event, serialize_event
from ui_stream.exceptions import InvalidEvent, LiveUnavailable
from ui_stream.types import ReadBatch

logger = logging.getLogger(__name__)
PAYLOAD_FIELD = "payload"
READ_COUNT = 100

_APPEND_SCRIPT = """
local stream_id = redis.call('XADD', KEYS[1], '*', ARGV[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return stream_id
"""


class RedisLiveStore:
    """Read/write an execution key using an injected Redis client and namespace mapping."""

    def __init__(
        self,
        *,
        client,
        physical_key=None,
        ttl_seconds=3600,
        event_type=Event,
        serializer=serialize_event,
        invalid_event_errors=(InvalidEvent,)
    ):
        self._client = client
        self._physical_key = physical_key or (lambda key: key)
        self._ttl_seconds = ttl_seconds
        self._event_type = event_type
        self._serializer = serializer
        self._invalid_event_errors = invalid_event_errors
        self._append_script = client.register_script(_APPEND_SCRIPT)

    @property
    def ttl(self) -> int:
        """execution Stream 的滑动 TTL；每次追加都会刷新。"""

        return self._ttl_seconds

    def physical_key(self, logical_key: str) -> str:
        """Apply the host-injected namespace/key mapping without importing its cache layer."""

        return self._physical_key(logical_key)

    def append(self, *, redis_key: str, event: Event) -> Event:
        try:
            return self._append(redis_key=redis_key, event=event)
        except (RedisError, OSError):
            raise LiveUnavailable("Redis append unavailable") from None

    def _append(self, *, redis_key: str, event: Event) -> Event:
        """追加事件并刷新当前 execution 流的滑动 TTL。"""

        payload = self._serializer(event, include_stream_id=False)
        stream_id = self._append_script(
            keys=[self.physical_key(redis_key)],
            args=[PAYLOAD_FIELD, payload, self.ttl],
        )
        return event.model_copy(update={"stream_id": self._decode(stream_id)})

    def read(self, *, redis_key: str, after_id: str, block_ms: int) -> ReadBatch:
        try:
            return self._read(redis_key=redis_key, after_id=after_id, block_ms=block_ms)
        except (RedisError, OSError, UnicodeDecodeError):
            # decode_responses may fail before XREAD returns IDs; do not invent a cursor.
            raise LiveUnavailable("Redis read unavailable") from None

    def _read(self, *, redis_key: str, after_id: str, block_ms: int) -> ReadBatch:
        """按 Redis 游标读取增量；脏 entry 不返回，但仍推进扫描游标。"""

        physical_stream_key = self.physical_key(redis_key)
        read_kwargs: dict[str, Any] = {"count": READ_COUNT}
        if block_ms > 0:
            read_kwargs["block"] = block_ms
        entries = self._client.xread({physical_stream_key: after_id}, **read_kwargs)
        events: list[Event] = []
        last_seen_stream_id: str | None = None
        for _, stream_entries in entries or []:
            for entry_id, fields in stream_entries:
                last_seen_stream_id = self._decode(entry_id)
                event = self._decode_entry(redis_key=redis_key, entry_id=entry_id, fields=fields)
                if event is not None:
                    events.append(event)
        return ReadBatch(events=events, last_seen_stream_id=last_seen_stream_id)

    def _decode_entry(self, *, redis_key: str, entry_id: Any, fields: dict) -> Event | None:
        """严格解析 entry；脏数据不阻塞后续事件，也不写入日志正文。"""

        stream_id = self._decode(entry_id)
        payload = fields.get(PAYLOAD_FIELD.encode("utf-8")) or fields.get(PAYLOAD_FIELD)
        if payload is None:
            logger.warning(
                "UI stream流事件缺少 payload 字段",
                extra={"redis_key": redis_key, "stream_id": stream_id},
            )
            return None
        try:
            event = self._event_type.model_validate_json(payload)
            # Pydantic JSON 会接受 NaN/Infinity；再次使用严格编码器验证标准 JSON 兼容性。
            self._serializer(event, include_stream_id=False)
        except (ValidationError, *self._invalid_event_errors):
            logger.warning(
                "UI stream流事件解析失败",
                extra={"redis_key": redis_key, "stream_id": stream_id},
            )
            return None
        return event.model_copy(update={"stream_id": stream_id})

    @staticmethod
    def _decode(value: Any) -> str:
        """统一把 Redis 返回的 bytes 解码为 str。"""

        return value.decode("utf-8") if isinstance(value, bytes) else str(value)


class BoundRedisPublisher:
    """Bind the Redis key at assembly; runtime only sees publish(event)."""

    def __init__(self, store: RedisLiveStore, redis_key: str):
        self._store = store
        self._redis_key = redis_key

    def publish(self, event: Event) -> Event:
        return self._store.append(redis_key=self._redis_key, event=event)

# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""默认 JSON profile 的 Redis 实时尾流适配，连接与命名空间由宿主注入。

append 以 Lua 原子执行 XADD 和滑动 EXPIRE；read 以真实 entry ID 推进游标。
脏 payload 跳过且不泄露正文；拿不到原始 ID 的预解码故障转为 LiveUnavailable。
不维护消费组、当前执行指针或持久快照，Redis TTL 也不代表结果保留期。
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

# 同一脚本内追加并刷新 TTL，避免其他客户端操作插入两步之间。
_APPEND_SCRIPT = """
local stream_id = redis.call('XADD', KEYS[1], '*', ARGV[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return stream_id
"""


class RedisLiveStore:
    """对宿主指定的 execution key 同步读写 Redis Stream。

    宿主注入 client、physical_key、事件模型与 serializer；key 的执行隔离和权限由宿主保证。
    本类没有跨调用锁；并发能力取决于 client，Lua 的原子范围仅为一次追加与 TTL 刷新。
    serializer 应与 Runtime 所用 codec 的表示保持一致。
    """

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
        """配置默认 Redis 实时适配器，并向 client 注册追加脚本。

        client 由宿主创建和关闭；physical_key 把逻辑 execution key 映射到实际 Redis key。
        ttl_seconds 是每次追加后刷新的保留秒数。event_type/serializer 用于读写同一种事件表示，
        应与 Runtime 的 codec 匹配；invalid_event_errors 指定读到坏 payload 时可跳过的格式异常。
        是否在注册脚本时访问网络，由注入的 client 实现决定。
        """

        self._client = client
        self._physical_key = physical_key or (lambda key: key)
        self._ttl_seconds = ttl_seconds
        self._event_type = event_type
        self._serializer = serializer
        self._invalid_event_errors = invalid_event_errors
        self._append_script = client.register_script(_APPEND_SCRIPT)

    @property
    def ttl(self) -> int:
        """实时 Stream 的滑动 TTL；每次成功追加都刷新，与持久归档保留期无关。"""

        return self._ttl_seconds

    def physical_key(self, logical_key: str) -> str:
        """应用宿主的逻辑 key 到实际命名空间映射，不读取宿主缓存层配置。"""

        return self._physical_key(logical_key)

    def append(self, *, redis_key: str, event: Event) -> Event:
        """向指定实时流追加事件，返回带 Redis 生成的 stream_id 的新事件。

        redis_key 经 physical_key 映射；payload 不包含尚未生成的回执。一次 Lua 执行完成 XADD
        和 TTL 刷新，Redis/网络异常映射为 LiveUnavailable，编码或其他接入错误向上传播。
        超时后可能已经写入，调用方不能仅凭异常认定本次没有追加。
        """

        try:
            return self._append(redis_key=redis_key, event=event)
        except (RedisError, OSError):
            raise LiveUnavailable("Redis append unavailable") from None

    def _append(self, *, redis_key: str, event: Event) -> Event:
        """把不含回执的 payload 与 XADD、EXPIRE 放在同一次脚本中。

        Redis 生成 ID 后才构造副本；网络异常后是否已追加可能不确定，适配器不提供去重。
        """

        payload = self._serializer(event, include_stream_id=False)
        stream_id = self._append_script(
            keys=[self.physical_key(redis_key)],
            args=[PAYLOAD_FIELD, payload, self.ttl],
        )
        return event.model_copy(update={"stream_id": self._decode(stream_id)})

    def read(self, *, redis_key: str, after_id: str, block_ms: int) -> ReadBatch:
        """读取 after_id 之后的一批事件，同时返回最后扫描到的真实 entry ID。

        redis_key 是逻辑 key，after_id 应已由宿主校验。block_ms>0 才传 XREAD BLOCK；
        0 或负值省略 BLOCK，表示非阻塞。没有原始 entry 时返回空事件和空扫描游标。
        坏 payload 会跳过但仍推进游标；Redis、网络及无法取得 entry ID 的预解码故障
        映射为 LiveUnavailable。
        """

        try:
            return self._read(redis_key=redis_key, after_id=after_id, block_ms=block_ms)
        except (RedisError, OSError, UnicodeDecodeError):
            # client 预解码失败时没有 entry ID 可用，结束读取而不猜测可续传的游标。
            raise LiveUnavailable("Redis read unavailable") from None

    def _read(self, *, redis_key: str, after_id: str, block_ms: int) -> ReadBatch:
        """按后端顺序扫描原始 entry；有效事件保留自身 ID，脏 entry 仍推进批次游标。"""

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
        """校验单条 payload 并附上真实 entry ID；已配置的格式错误跳过并记元信息。

        严格 serializer 还会拒绝模型解析允许的 NaN/Infinity；不信任 payload 自带的游标。
        """

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
        """将后端 bytes 按 UTF-8 解码，其余值转成字符串；不伪造读取游标。"""

        return value.decode("utf-8") if isinstance(value, bytes) else str(value)


class BoundRedisPublisher:
    """在组装时绑定 execution key，把 RedisLiveStore 适配为 publish(event) 端口。"""

    def __init__(self, store: RedisLiveStore, redis_key: str):
        """固定 store 与逻辑 key；宿主负责在执行轮换时使用正确的绑定。"""

        self._store = store
        self._redis_key = redis_key

    def publish(self, event: Event) -> Event:
        """使用绑定 key 发布一次，保留底层事件回执和异常语义。"""

        return self._store.append(redis_key=self._redis_key, event=event)

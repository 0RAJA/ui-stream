# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""按 Redis 原始游标迭代实时事件，并可结合宿主状态收敛连接生命周期。

正常路径保持事件顺序；无有效事件时可产出 None 心跳，不回查持久快照。
宿主终态提示先排空实时尾部，执行切换提示立即关闭；状态校验异常直接传播。
初次鉴权、执行选择、归档回放与断连后的恢复策略均由宿主负责。
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
    """校验客户端续传游标，返回可用于 Redis 增量读取的原字符串。

    Args:
        value: 客户端上次收到的 stream_id；空值或其他假值视为从 0-0 开始。

    Returns:
        合法游标原样返回，包括前导零；非空输入必须是两个 ASCII 十进制 uint64 分量。

    Raises:
        InvalidCursor: 类型、格式或数值范围不合法；此处不检查事件是否存在或调用者权限。
    """

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
    """从 Redis 增量读取并依次产出默认 Event；暂时无业务事件时可产出 None 心跳。

    宿主应先鉴权、选择 execution 并用 normalize_cursor 校验游标，再调用本迭代器。
    它只读取实时尾流；初始快照、持久归档回放与重连恢复由宿主另行实现。

    Args:
        redis_store: 提供 read(*, redis_key, after_id, block_ms) 的后端，返回 ReadBatch。
        redis_key: 已选 execution 的逻辑 key，实际命名空间由后端适配器映射。
        cursor: 已校验的起始游标，后续从读到的原始 entry ID 继续。
        block_ms: 单次读取等待的毫秒数；默认 Redis 适配器中 <=0 表示不阻塞。
        idle_timeout: 连续没有业务事件时的结束秒数；心跳和控制消息不续期。
        clock: 单调时钟，便于测试时间边界。
        live_errors: 读取遇到这些异常时结束本次迭代，不伪造业务终态。
        state_check: 可选无参宿主检查，返回关闭控制事件或 None；权限等异常直接传播。
        state_poll_interval: 启用检查后的秒间隔，必须为正；首次检查也在间隔到期后。

    收到原始 stream_end 时原样输出并结束。宿主检查返回 stream_end 提示时，先非阻塞排空
    已发布尾流，直到没有原始 entry 才输出合成提示；脏数据批次不等于尾流为空。
    其他非空状态提示（如 stream_reset）立即输出并关闭，停止读取旧 execution。
    """

    if state_check is not None and state_poll_interval <= 0:
        raise ValueError("state_poll_interval must be positive")
    now = clock()
    last_business_event_at = now
    next_state_check = now + state_poll_interval if state_check is not None else None
    terminal_event = None

    def refresh_state(now):
        """到期才检查宿主状态；暂存持久终态提示，其他非空控制事件交给外层立即关闭。"""

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
            # 将阻塞等待限制在下次状态检查期限内，持续有消息时也仍逐条检查权限/代际。
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
            # 配置内实时故障只结束本次迭代；快照重取与重连策略由宿主决定。
            logger.warning(
                "UI stream live read unavailable",
                extra={"redis_key": redis_key},
            )
            return
        if state_check is None:
            # 未启用状态校验时，每批共用一次时间采样来判断业务空闲。
            now = clock()

        # 扫描游标包括脏 entry；有效事件仍携带各自 ID，供客户端按实际接收进度续传。
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
                    # 没有有效事件但仍有原始 ID，表示刚扫过脏数据，不能据此判断尾流已空。
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
                # 使用原始终止事件及其真实游标，避免被宿主的合成终态提示替换。
                return
            if event.event is None:
                has_business_event = True
                last_business_event_at = now

        # 心跳、控制事件与脏数据都不续业务空闲计时；终态提示的排空过程不受此分支截断。
        if terminal_event is None and not has_business_event and now - last_business_event_at >= idle_timeout:
            return

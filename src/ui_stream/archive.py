# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pure archive integrity and bounded prefix operations; storage belongs to the host."""

import json
from collections.abc import Callable
from typing import Any

from ui_stream.types import Integrity

_ARCHIVE_STATUS_PRIORITY = {Integrity.COMPLETE: 0, Integrity.DEGRADED: 1, Integrity.TRUNCATED: 2}


def merge_archive_status(current: Integrity, incoming: Integrity) -> Integrity:
    """返回两个归档状态中更严重的一个（单向升级语义）。

    设计意图：归档状态在同一次执行内只允许从 COMPLETE → DEGRADED → TRUNCATED 单向升级，
    避免前端展示的降级提示反复抖动。调用方无需关心合并顺序，结果始终是更严重的那个。

    Args:
        current: 当前已持久化的归档状态。
        incoming: 本次操作产生的新状态。

    Returns:
        两者中严重程度更高的状态值。
    """

    if _ARCHIVE_STATUS_PRIORITY[current] >= _ARCHIVE_STATUS_PRIORITY[incoming]:
        return current
    return incoming


def _encoded_event_bytes(event: Any) -> int:
    """计算单个事件在归档 JSON 数组中占用的 UTF-8 紧凑编码字节数。

    不包含数组分隔符（逗号、方括号），仅计算事件自身序列化后的字节长度。
    用于 fit_archive_events 的增量字节累加，避免每次追加都重新编码整个数组。

    Args:
        event: 待计算的流式事件。

    Returns:
        该事件紧凑 JSON 编码后的 UTF-8 字节数。
    """

    return len(json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def encoded_archive_bytes(events: list[Any], *, event_size: Callable[[Any], int] | None = None) -> int:
    """计算事件列表序列化为 JSON 数组后的 UTF-8 紧凑编码总字节数。

    计算公式：2（首尾方括号） + 各元素编码字节之和 + (元素数 - 1)（逗号分隔符）。
    这样无需真正执行 json.dumps(整个列表)，即可精确得到最终落库的字节数。

    Args:
        events: 已有的事件列表。
        event_size: 宿主实际持久记录的单事件字节计量；默认计量 model_dump。

    Returns:
        该列表序列化为紧凑 JSON 数组后的 UTF-8 字节数（空列表返回 2，即 "[]"）。
    """

    if not events:
        # 空数组仍占用 "[]" 两个字节。
        return 2
    # 数组等于各元素编码之和，加上首尾方括号与元素间逗号，无需再整体编码一次。
    measure_event = event_size if event_size is not None else _encoded_event_bytes
    return 2 + sum(measure_event(event) for event in events) + (len(events) - 1)


def fit_archive_events(
    *,
    existing: list[Any],
    incoming: list[Any],
    max_bytes: int,
    event_size: Callable[[Any], int] | None = None,
) -> tuple[list[Any], int, bool]:
    """在容量限制内按顺序追加尽可能多的事件，返回合并结果。

    设计意图：
    - 字节数按增量累加（O(n) 对 incoming），而非每追加一条都重新编码整个数组。
      单执行可能累积上万条事件，全量编码会让持有行锁的 checkpoint 退化成 O(n²)。
    - 一旦某条事件放不下即停止，不跳跃保留后续事件，保证前端回放的时序连续性。

    Args:
        existing: 数据库中已有的归档事件列表。
        incoming: 本次待追加的新事件列表。
        max_bytes: 归档 JSON 数组的 UTF-8 字节上限。
        event_size: 与 encoded_archive_bytes 相同的宿主持久记录计量。

    Returns:
        三元组 (merged, accepted_count, truncated):
        - merged: 合并后的完整事件列表（existing + 已接收的 incoming 前缀）。
        - accepted_count: 本次实际接收的 incoming 事件数量。
        - truncated: 是否因容量不足而截断（True 表示有事件被丢弃）。
    """

    if not incoming:
        return list(existing), 0, False

    accepted: list[Any] = list(existing)
    accepted_count = 0
    measure_event = event_size if event_size is not None else _encoded_event_bytes
    current_bytes = encoded_archive_bytes(accepted, event_size=measure_event)
    for event in incoming:
        # 追加一个元素会增加它自身的编码长度，以及非空数组所需的一个逗号。
        candidate_bytes = current_bytes + measure_event(event) + (1 if accepted else 0)
        if candidate_bytes > max_bytes:
            # 单条事件放不下即停止，保持事件顺序连续，不跳跃保留后续事件。
            return accepted, accepted_count, True
        accepted.append(event)
        accepted_count += 1
        current_bytes = candidate_bytes
    return accepted, accepted_count, False

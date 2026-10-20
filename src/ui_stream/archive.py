# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""提供归档完整性合并、紧凑 JSON 数组计量与有界前缀追加的纯函数。

宿主在自己的存储事务内调用，再决定如何保存完整性和已接收的前缀。
event_size 可对齐包含宿主隐藏字段的真实编码；已有列表不会被这些函数修改。
快照查询、权限、持久化和归档回放由宿主实现，这里没有读取或回放端口。
"""

import json
from collections.abc import Callable
from typing import Any

from ui_stream.types import Integrity

_ARCHIVE_STATUS_PRIORITY = {Integrity.COMPLETE: 0, Integrity.DEGRADED: 1, Integrity.TRUNCATED: 2}


def merge_archive_status(current: Integrity, incoming: Integrity) -> Integrity:
    """返回两个归档状态中更严重的一个，保留本次执行已知的降级情况。

    设计意图：归档状态在同一次执行内只允许从 COMPLETE → DEGRADED → TRUNCATED 单向升级，
    避免前端展示的降级提示在后续操作成功时反复消失、出现。调用方无需关心合并顺序，
    结果始终是更严重的那个，也允许从 COMPLETE 直接变为 TRUNCATED。

    Args:
        current: 当前已有的归档状态，可以来自 Runtime 内存或宿主持久化记录。
        incoming: 本次发布、归档或提交操作得到的新状态。

    Returns:
        两者中严重程度更高的状态值；本函数不修改事件列表或持久化记录。
    """

    if _ARCHIVE_STATUS_PRIORITY[current] >= _ARCHIVE_STATUS_PRIORITY[incoming]:
        return current
    return incoming


def _encoded_event_bytes(event: Any) -> int:
    """计算单个事件的紧凑 JSON UTF-8 字节数，不含数组分隔符。

    通过 model_dump(mode="json") 获取事件表示；与数组开销分开计量，
    使批量追加时只需编码每个事件一次。
    """

    return len(json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def encoded_archive_bytes(events: list[Any], *, event_size: Callable[[Any], int] | None = None) -> int:
    """计算事件列表保存为紧凑 JSON 数组时的 UTF-8 字节数。

    设计意图：把方括号、逗号和单事件字节数相加，避免为计量而反复编码整个数组。
    只有单事件计量与宿主最终保存的 JSON 表示一致时，结果才等于实际持久化大小。

    Args:
        events: 需要计量的完整事件列表。
        event_size: 返回一条完整持久记录的字节数，不含数组分隔符。
            默认使用 model_dump(mode="json") 的紧凑编码；若宿主另存隐藏字段，
            应传入包含这些字段的计量函数。

    Returns:
        2（方括号）+ 各事件字节之和 + 元素间逗号数；空数组返回 2。
    """

    if not events:
        return 2
    measure_event = event_size if event_size is not None else _encoded_event_bytes
    return 2 + sum(measure_event(event) for event in events) + (len(events) - 1)


def fit_archive_events(
    *,
    existing: list[Any],
    incoming: list[Any],
    max_bytes: int,
    event_size: Callable[[Any], int] | None = None,
) -> tuple[list[Any], int, bool]:
    """在归档容量内保留已有事件，再顺序追加能放下的新事件前缀。

    设计意图：先计量已有列表，再逐条累加新增字节，避免每次追加都重编码整个数组。
    首条放不下就停止，保留连续的事件前缀，方便 UI 按原顺序回放；不会跳选更小的后续事件。

    Args:
        existing: 宿主已有的归档事件；返回结果保留这些事件，即使它们已经超过上限。
        incoming: 本批待追加的事件，顺序与产生顺序一致。
        max_bytes: 完整归档 JSON 数组的 UTF-8 字节上限，含方括号和逗号。
        event_size: 单条持久记录的字节计量，约定与 encoded_archive_bytes 相同。

    Returns:
        (merged, accepted_count, truncated)：新列表、接收的 incoming 条数、是否丢弃了 incoming。
        merged 包含 existing 和接收的新事件前缀；输入列表不被修改，也不会自动去重。
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

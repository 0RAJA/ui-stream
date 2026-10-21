# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""默认 JSON 事件 profile，统一实时、归档与 SSE 使用的数据表示。

业务值经 JsonEventCodec 构造为 Event；严格 UTF-8 紧凑 JSON 编码拒绝 NaN/Infinity。
LIVE 表示省略 stream_id，ARCHIVE 表示保留回执；编码过程不执行发布或持久化。
此模块使用可选 Pydantic 依赖，自定义 codec 可以替换整个事件模型。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from ui_stream._json import serialize_data
from ui_stream.exceptions import InvalidEvent
from ui_stream.types import EncodingTarget


def serialize_event(event: Event, *, include_stream_id: bool = True) -> bytes:
    """编码默认事件信封；实时 payload 可省略回执字段，归档可保留 stream_id。"""

    payload = {"event": event.event}
    if include_stream_id:
        payload["stream_id"] = event.stream_id
    payload["data"] = event.data
    return serialize_data(payload)


class ControlEvent(StrEnum):
    """默认读取协议的关闭信号，由实时流或宿主状态校验提供。

    业务事件的 event 为 None；这两个控制值都会结束当前实时迭代。
    """

    STREAM_RESET = "platform.stream_reset"  # 绑定执行已切换等情况；宿主决定如何重新选择执行。
    STREAM_END = "platform.stream_end"  # 本执行结束；Redis 原始事件立即关闭，状态提示则先排空尾流。


class Event(BaseModel):
    """默认 JSON/SSE 事件：业务数据、可选控制类型和可选实时回执。

    业务消息的 event 为 None，结束/执行切换消息使用 ControlEvent；data 保存各自内容。
    Redis 追加成功后会返回附有 stream_id 的副本，便于归档和客户端续传。
    字段赋值被冻结且拒绝额外字段，但 data 的嵌套对象没有深度冻结；接受后不得继续修改。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    event: ControlEvent | None = None  # None 表示业务消息，控制值表示关闭信号。
    stream_id: str | None = None  # Redis 适配器写回真实游标；宿主合成事件通常留空。
    data: Any = None  # 业务或控制数据；构造时不保证可编码，严格校验由 serializer 执行。


class JsonEventCodec:
    """把业务输入包装成默认 Event，并把任务终态转成 stream_end 控制事件。

    business 接受可编码为标准 JSON 的数据，严格校验在 encode 时完成；terminal 接受
    SUCCESS/FAILED。event_factory 和 serializer 可替换，但构造、计量与实际端口应保持一致。
    reset 按宿主原因构造 stream_reset；事件构造不分配实时或归档身份。
    """

    def __init__(self, *, event_factory=Event, serializer=serialize_event):
        """注入匹配的事件构造器与 serializer；初始化不执行编码或 I/O。"""

        self._event_factory = event_factory
        self._serializer = serializer

    def business(self, data: Any) -> Event:
        """包装业务 data，保留 event=None；标准 JSON 校验在 encode 时完成。"""

        return self._event_factory(data=data)

    def terminal(self, status: str) -> Event:
        """只接受 SUCCESS/FAILED，生成携带 status 的 stream_end；其他值抛 InvalidEvent。"""

        if status not in ("SUCCESS", "FAILED"):
            raise InvalidEvent("Unsupported terminal status")
        return self._event_factory(event=ControlEvent.STREAM_END, data={"status": status})

    def reset(self, reason: str) -> Event:
        """构造无游标的执行切换提示；原因由宿主解释，不分配可续传身份。"""

        if not isinstance(reason, str):
            raise InvalidEvent("Reset reason must be text")
        return self._event_factory(event=ControlEvent.STREAM_RESET, data={"reason": reason})

    def encode(self, event: Event, target: EncodingTarget) -> bytes:
        """按计量目标决定是否包含 stream_id，返回字节而不执行 I/O。"""

        return self._serializer(event, include_stream_id=target == EncodingTarget.ARCHIVE)

# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""Strict compact JSON, shared by persistence, live transport and SSE."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from ui_stream.exceptions import InvalidEvent
from ui_stream.types import EncodingTarget


def serialize_data(data: Any) -> bytes:
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise InvalidEvent("Stream data must be standard JSON") from None


def serialize_event(event: Event, *, include_stream_id: bool = True) -> bytes:
    payload = {"event": event.event}
    if include_stream_id:
        payload["stream_id"] = event.stream_id
    payload["data"] = event.data
    return serialize_data(payload)


class ControlEvent(StrEnum):
    STREAM_RESET = "platform.stream_reset"
    STREAM_END = "platform.stream_end"


class Event(BaseModel):
    """Default JSON/SSE profile; custom codecs can use unrelated event types."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    event: ControlEvent | None = None
    stream_id: str | None = None
    data: Any = None


class JsonEventCodec:
    """Default message/end schema, replaceable as a whole at runtime assembly."""

    def __init__(self, *, event_factory=Event, serializer=serialize_event):
        self._event_factory = event_factory
        self._serializer = serializer

    def business(self, data: Any) -> Event:
        return self._event_factory(data=data)

    def terminal(self, status: str) -> Event:
        if status not in ("SUCCESS", "FAILED"):
            raise InvalidEvent("Unsupported terminal status")
        return self._event_factory(event=ControlEvent.STREAM_END, data={"status": status})

    def encode(self, event: Event, target: EncodingTarget) -> bytes:
        return self._serializer(event, include_stream_id=target == EncodingTarget.ARCHIVE)

# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""Core contracts for one execution, with no event schema or transport assumptions.

The host owns execution identity and fencing. Events are caller-owned values;
codecs and publishers must not mutate them after acceptance.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Generic, Protocol, TypeVar
from uuid import UUID

EventT = TypeVar("EventT")
_ArchiveEventT = TypeVar("_ArchiveEventT", contravariant=True)


class Integrity(StrEnum):
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"
    TRUNCATED = "TRUNCATED"


class EncodingTarget(StrEnum):
    LIVE = "LIVE"
    ARCHIVE = "ARCHIVE"


class ExecutionBinding(Protocol):
    @property
    def resource_id(self) -> str:
        ...

    @property
    def task_id(self) -> str:
        ...

    @property
    def execution_id(self) -> UUID:
        ...


@dataclass(frozen=True, slots=True)
class Binding:
    resource_id: str
    task_id: str
    execution_id: UUID


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A handled batch; exhausted capacity stops future buffering, not necessarily loss yet."""

    archive_status: Integrity
    capacity_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class ReadBatch(Generic[EventT]):
    events: list[EventT]
    last_seen_stream_id: str | None


@dataclass(frozen=True, slots=True)
class StreamPolicy:
    max_event_bytes: int = 256 * 1024
    max_events: int = 10_000
    max_live_bytes: int = 10 * 1024 * 1024
    max_archive_bytes: int = 10 * 1024 * 1024
    activity_interval_seconds: int = 60
    checkpoint_event_count: int = 20
    checkpoint_bytes: int = 256 * 1024


@dataclass(frozen=True, slots=True)
class Summary:
    """Once-only post-close notification; contains neither event bodies nor runtime access."""

    status: str
    error_code: str
    archive_status: Integrity
    degraded: bool
    truncated: bool
    event_count: int
    event_bytes: int


AfterClose = Callable[[Summary], None]


class EventCodec(Protocol[EventT]):
    """Build immutable domain events and encode without performing transport/storage I/O."""

    def business(self, data: Any) -> EventT:
        ...

    def terminal(self, status: str) -> EventT:
        ...

    def encode(self, event: EventT, target: EncodingTarget) -> bytes:
        ...


class LivePublisher(Protocol[EventT]):
    """Publish synchronously; return an immutable event with an optional transport receipt."""

    def publish(self, event: EventT) -> EventT:
        ...


class ArchiveStore(Protocol[_ArchiveEventT]):
    """Handle each checkpoint batch; atomically commit tail, terminal and result with fencing.

    Return only after successful storage work; report dropped events through integrity.
    """

    def checkpoint(
        self, *, binding: ExecutionBinding, events: tuple[_ArchiveEventT, ...], archive_status: Integrity
    ) -> Checkpoint:
        ...

    def touch_activity(self, *, binding: ExecutionBinding) -> None:
        ...

    def commit(
        self,
        *,
        binding: ExecutionBinding,
        events: tuple[_ArchiveEventT, ...],
        terminal_event: _ArchiveEventT,
        archive_status: Integrity,
        completion: Any,
    ) -> Integrity:
        ...

# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""Bounded live/snapshot coordination, independent of a task framework or ORM.

Live and intermediate archive failures may degrade UI history. The host commit
must atomically save its result and tail before returning; terminal publication
is best effort. A runtime serializes send calls with a reentrant process lock.
"""

import logging
import threading
import time
from typing import Any, Generic
from uuid import UUID

from ui_stream.archive import merge_archive_status
from ui_stream.exceptions import ArchiveUnavailable, ClosedStream, LiveUnavailable
from ui_stream.types import (
    AfterClose,
    ArchiveStore,
    EncodingTarget,
    EventCodec,
    EventT,
    ExecutionBinding,
    Integrity,
    LivePublisher,
    StreamPolicy,
    Summary,
)

logger = logging.getLogger(__name__)


class StreamRuntime(Generic[EventT]):
    """Coordinate a single bound execution; the host owns rotation and result transactions."""

    def __init__(
        self,
        *,
        binding: ExecutionBinding,
        publisher: LivePublisher[EventT],
        codec: EventCodec[EventT],
        archive_store: ArchiveStore[EventT],
        archive_status: Integrity = Integrity.COMPLETE,
        policy: StreamPolicy = StreamPolicy(),
        after_close: AfterClose | None = None,
        archive_errors: tuple[type[Exception], ...] = (ArchiveUnavailable,),
        live_errors: tuple[type[Exception], ...] = (LiveUnavailable, OSError),
        closed_error: type[Exception] = ClosedStream,
    ):
        self._binding = binding
        self._policy = policy
        self._codec = codec
        self._after_close = after_close
        self._archive_errors = archive_errors
        self._live_errors = live_errors
        self._closed_error = closed_error
        self._publisher = publisher
        self._archive_store = archive_store
        self._archive_status = archive_status
        # 业务 Task 可能在自己的线程池里 send，进程内串行化保证缓冲顺序。
        self._lock = threading.RLock()
        self._pending: list[EventT] = []
        self._pending_bytes = 0
        self._closed = False
        # 业务事件计数与字节量用于容量降级判断。
        self._business_event_count = 0
        self._business_event_bytes = 0
        self._live_business_bytes = 0
        self._degraded = False
        self._truncated = False
        self._archive_stopped = False
        self._live_stopped = False
        # 只在实际落库成功后推进，数据库故障时下一条事件会继续尝试。
        self._last_activity_checkpoint_at = time.monotonic()

    @property
    def execution_id(self) -> UUID:
        """返回已持久化的本次执行标识，供业务关联外部服务会话。"""
        return self._binding.execution_id

    @property
    def binding(self) -> ExecutionBinding:
        """Host-owned execution identity; contains no transport configuration requirement."""

        return self._binding

    @property
    def pending_count(self) -> int:
        """尚未归档的缓冲事件条数。"""

        with self._lock:
            return len(self._pending)

    @property
    def archive_status(self) -> Integrity:
        """当前执行的归档完整性；只会单向升级。"""

        with self._lock:
            return self._archive_status

    @property
    def closed(self) -> bool:
        """Runtime 是否已在终态或 Retry 后关闭。"""

        with self._lock:
            return self._closed

    def send(self, data: Any) -> EventT | None:
        """Encode and send a domain value in execution order, with bounded buffering."""

        with self._lock:
            self._require_open()
            ui_event = self._codec.business(data)
            # 先编码：非法 data 属于接入错误，必须在任何降级判断前暴露。
            live_payload_size = len(self._codec.encode(ui_event, EncodingTarget.LIVE))
            if not self._accept_business_event(payload_size=live_payload_size):
                return None
            self._business_event_count += 1
            self._business_event_bytes += live_payload_size
            ui_event = self._write_live(ui_event, payload_size=live_payload_size)
            # A publisher may attach a receipt. Bound pending memory by its archive encoding,
            # which can differ from the live representation.
            archive_payload_size = len(self._codec.encode(ui_event, EncodingTarget.ARCHIVE))
            self._buffer(ui_event, payload_size=archive_payload_size)
            return ui_event

    def finish_retry(self) -> None:
        """Flush then close for host retry; no terminal event or business result is written."""

        with self._lock:
            if self._closed:
                return
            try:
                self._checkpoint()
            except self._archive_errors:
                self._degrade("UI stream retry checkpoint unavailable", status=Integrity.DEGRADED)
            self._closed = True
            summary = self._freeze_summary(status="RETRY", error_code="")
        self._notify_closed(summary)

    def finish(self, *, status: str, completion: Any, error_code: str = "") -> None:
        """Commit the tail and host result before close and best-effort terminal publication.

        Commit errors propagate without clearing pending or closing. The store must
        return only after a real atomic commit, including fencing. The codec owns
        terminal vocabulary; the core does not interpret domain statuses.
        """

        with self._lock:
            self._require_open()
            terminal_event = self._codec.terminal(status)
            self._codec.encode(terminal_event, EncodingTarget.ARCHIVE)
            self._codec.encode(terminal_event, EncodingTarget.LIVE)
            persisted_status = self._archive_store.commit(
                binding=self._binding,
                events=tuple(self._pending),
                terminal_event=terminal_event,
                archive_status=self._archive_status,
                completion=completion,
            )
            self._archive_status = merge_archive_status(self._archive_status, persisted_status)
            self._observe_archive_status(persisted_status)
            self._pending.clear()
            self._pending_bytes = 0
            self._closed = True
            self._publish_terminal(terminal_event)
            summary = self._freeze_summary(status=str(status), error_code=error_code)
        self._notify_closed(summary)

    def _publish_terminal(self, terminal_event: EventT) -> None:
        """Best-effort live notification after the durable result has committed."""

        try:
            self._publisher.publish(terminal_event)
        except Exception:
            # Notification failure cannot roll back a committed result.
            self._degrade("UI stream终止事件实时发布失败", status=Integrity.DEGRADED)

    def _accept_business_event(self, *, payload_size: int) -> bool:
        """按单事件与总条数上限判断是否接收；超限只截断不失败。"""

        if payload_size > self.policy.max_event_bytes:
            self._degrade(
                "UI stream流式事件超过单条上限",
                status=Integrity.TRUNCATED,
                extra={"payload_size": payload_size},
            )
            return False
        if self._business_event_count >= self.policy.max_events:
            if not self._archive_stopped:
                self._archive_stopped = True
                self._degrade(
                    "UI stream流式事件超过条数上限",
                    status=Integrity.TRUNCATED,
                )
        return True

    def _write_live(self, event: EventT, *, payload_size: int) -> EventT:
        """写实时流；连接故障或超限降级，其他异常向调用方传播。"""

        if self._live_stopped:
            return event
        if self._live_business_bytes + payload_size > self.policy.max_live_bytes:
            self._live_stopped = True
            self._degrade("UI stream流式实时字节超过上限", status=Integrity.DEGRADED)
            return event
        try:
            published = self._publisher.publish(event)
        except self._live_errors:
            self._degrade("UI stream流式实时写入失败", status=Integrity.DEGRADED)
            return event
        self._live_business_bytes += payload_size
        return published

    def _buffer(self, event: EventT, *, payload_size: int) -> None:
        """事件进入归档缓冲，达到批阈值时仅对数据库故障降级。"""

        if self._archive_stopped:
            self._touch_activity_if_due()
            return
        # 持久存储 持续故障时 checkpoint 无法清空 pending。进程内缓冲仍必须受与
        # 持久归档相同的字节上限约束，避免单任务把 Worker 内存无限吃满。
        if self._pending_bytes + payload_size > self.policy.max_archive_bytes:
            # 缓冲触顶可能只是数据库短暂故障。先用现有 pending 做最后一次恢复性
            # checkpoint；成功后继续接收当前事件，失败或持久归档已耗尽才截断。
            try:
                self._checkpoint()
            except self._archive_errors:
                self._degrade(
                    "UI stream流式缓冲触顶前归档失败",
                    status=Integrity.DEGRADED,
                )
        if self._archive_stopped or self._pending_bytes + payload_size > self.policy.max_archive_bytes:
            self._archive_stopped = True
            self._degrade(
                "UI stream流式待归档缓冲超过上限",
                status=Integrity.TRUNCATED,
            )
            self._touch_activity_if_due()
            return
        self._pending.append(event)
        self._pending_bytes += payload_size
        if (
            len(self._pending) >= self.policy.checkpoint_event_count
            or self._pending_bytes >= self.policy.checkpoint_bytes
            or self._activity_checkpoint_due()
        ):
            try:
                self._checkpoint()
            except self._archive_errors:
                self._degrade("UI stream流式自动归档失败", status=Integrity.DEGRADED)

    def _checkpoint(self) -> None:
        """归档当前缓冲；只有写入成功才清空，降低进程内事件丢失风险。

        当前归档不提供事件级去重，也不宣称 exactly-once；行锁和 fencing 只负责
        串行写入并隔离旧执行，不能把重复事件自动合并。
        """

        if not self._pending:
            return
        result = self._archive_store.checkpoint(
            binding=self._binding,
            events=tuple(self._pending),
            archive_status=self._archive_status,
        )
        self._pending.clear()
        self._pending_bytes = 0
        self._archive_status = merge_archive_status(self._archive_status, result.archive_status)
        self._last_activity_checkpoint_at = time.monotonic()
        self._observe_archive_status(result.archive_status)
        # Once storage reports exhaustion, keep the live tail but stop new buffering.
        # This avoids repeated expensive writes against a full snapshot.
        if result.capacity_exhausted:
            self._archive_stopped = True

    def _activity_checkpoint_due(self) -> bool:
        """低频事件按时间触发 checkpoint，避免真实长任务被巡检误判失活。"""

        return time.monotonic() - self._last_activity_checkpoint_at >= self.policy.activity_interval_seconds

    def _touch_activity_if_due(self) -> None:
        """归档停止后仍按时间刷新 execution 活动；数据库故障沿用流降级语义。"""

        if not self._activity_checkpoint_due():
            return
        try:
            self._archive_store.touch_activity(binding=self._binding)
        except self._archive_errors:
            self._degrade("UI stream流式活动刷新失败", status=Integrity.DEGRADED)
            return
        self._last_activity_checkpoint_at = time.monotonic()

    def _degrade(
        self,
        message: str,
        *,
        status: Integrity,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """统一降级入口：提升归档状态并记录不含事件正文的告警。"""

        self._archive_status = merge_archive_status(self._archive_status, status)
        self._observe_archive_status(status)
        logger.warning(
            message,
            extra={
                "resource_id": self._binding.resource_id,
                "task_id": self._binding.task_id,
                "execution_id": str(self._binding.execution_id),
                "archive_status": str(self._archive_status),
                **(extra or {}),
            },
        )

    def _observe_archive_status(self, status: Integrity) -> None:
        """记录本次 execution 曾发生的降级类型，终态汇总不读取事件正文。"""

        if status == Integrity.DEGRADED:
            self._degraded = True
        elif status == Integrity.TRUNCATED:
            self._truncated = True

    def _require_open(self) -> None:
        """终态或 Retry 后继续使用 Runtime 属于业务接入错误。"""

        if self._closed:
            raise self._closed_error()

    @property
    def policy(self) -> StreamPolicy:
        return self._policy

    def _freeze_summary(self, *, status: str, error_code: str) -> Summary:
        return Summary(
            status=status,
            error_code=error_code,
            archive_status=self._archive_status,
            degraded=self._degraded,
            truncated=self._truncated,
            event_count=self._business_event_count,
            event_bytes=self._business_event_bytes,
        )

    def _notify_closed(self, summary: Summary) -> None:
        if self._after_close is None:
            return
        try:
            self._after_close(summary)
        except Exception:
            # A notification cannot undo the result or make the caller retry a committed execution.
            logger.warning("UI stream after-close notification failed")

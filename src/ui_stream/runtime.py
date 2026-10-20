# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""协调单次执行的同步写入、归档缓冲和最终提交，不依赖任务框架或 ORM。

send 经 codec 校验/计量、实时发布，再进入缓冲与 checkpoint；finish 先 commit 再通知终态。
可恢复实时/中间归档故障只降低历史完整性；最终事务失败必须暴露给宿主。
实例内 RLock 串行化链路，跨实例 fencing、结果事务与执行重试仍由宿主实现。
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
    """协调一次任务执行的进度发送、分批归档和最终结果提交。

    使用方（宿主应用）先创建 execution 并组装三个端口，再反复 send，最后调用 finish。
    Runtime 把暂未归档的事件保存在 pending 缓冲中，达到阈值后交给 ArchiveStore。
    实例内的 RLock 覆盖端口 I/O，保证同一实例按取得锁的顺序处理；不同实例/进程的
    执行隔离由宿主存储端校验。成功 finish 或 finish_retry 后，这个实例不再接受写入。
    """

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
        """为一个已有 execution 组装运行时；初始化不创建执行记录或访问后端。

        Args:
            binding: 宿主创建的资源、任务和 execution 身份，所有存储操作都携带这个绑定。
            publisher: 同步发布事件的实时通道，可以返回附加回执的新事件。
            codec: 将业务输入构造成事件，并提供实时/归档编码与字节计量。
            archive_store: 宿主实现的归档与最终结果存储端口。
            archive_status: 此次执行已有的归档状态，后续只会向更严重的状态合并。
            policy: 业务事件容量、缓冲批量和活动刷新阈值。
            after_close: 关闭后接收 Summary 的可选观察者，在释放实例锁后调用。
            archive_errors: 中间归档时允许降级继续的异常类型；最终 commit 异常仍传播。
            live_errors: 业务实时发布时允许降级继续的异常类型。
            closed_error: send/finish 使用已关闭实例时抛出的无参异常类型。
        """

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
        # 锁覆盖端口 I/O 与缓冲更新，避免同一实例内发布返回顺序打乱归档顺序。
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
        self._observe_archive_status(archive_status)
        self._archive_stopped = False
        self._live_stopped = False
        # 活动基准始于实例创建；此后仅在 checkpoint/touch_activity 成功后刷新。
        self._last_activity_checkpoint_at = time.monotonic()

    @property
    def execution_id(self) -> UUID:
        """返回本实例绑定的执行 ID，供业务关联外部调用或会话。

        该 ID 由宿主传入；Runtime 不查询它对应的记录是否已持久化。
        """
        return self._binding.execution_id

    @property
    def binding(self) -> ExecutionBinding:
        """返回本实例使用的资源、任务和执行身份，供宿主关联存储与诊断。"""

        return self._binding

    @property
    def pending_count(self) -> int:
        """返回仍在本实例缓冲中的事件数，不包含此前已 checkpoint 的记录。"""

        with self._lock:
            return len(self._pending)

    @property
    def archive_status(self) -> Integrity:
        """返回本次执行当前已知的归档状态，后续操作成功不会把它恢复为 COMPLETE。

        提交后的实时终态通知失败仍会使内存状态降级，但不会回写已经提交的归档记录。
        """

        with self._lock:
            return self._archive_status

    @property
    def closed(self) -> bool:
        """是否已在正常 finish 或 finish_retry 后关闭；最终 commit 抛错时保持未关闭。"""

        with self._lock:
            return self._closed

    def send(self, data: Any) -> EventT | None:
        """处理一条业务进度：构造并校验、尝试实时发布，再加入归档缓冲。

        设计意图：先验证输入，避免把编码错误误当作后端故障；发布后再按可能新增的回执计量归档。
        达到 checkpoint 阈值会同步调用存储端，因此一次 send 也可能包含归档 I/O。

        Args:
            data: 交给 codec.business 的业务输入，结构和合法性由所选 codec 决定。

        Returns:
            用于后续归档的事件；发布成功时可能带回执，实时降级或停止时仍可返回原事件。
            单条 LIVE 编码超过 max_event_bytes 时返回 None。返回事件不代表已实时送达或归档成功。

        max_events 触顶后仍可实时发送，但不再缓冲新事件。编码错误和未配置为可恢复的端口异常
        直接传播；若归档编码在发布后失败，已发生的实时写入不会回滚。
        """

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
            # publisher 可返回带回执的副本；按发布后的 ARCHIVE 编码计量，避免漏算回执占用。
            archive_payload_size = len(self._codec.encode(ui_event, EncodingTarget.ARCHIVE))
            self._buffer(ui_event, payload_size=archive_payload_size)
            return ui_event

    def finish_retry(self) -> None:
        """在宿主准备重试任务前，尝试保存当前缓冲并关闭本 Runtime。

        它只调用 checkpoint，不构造终态、不调用 commit，也不保存最终业务结果。
        后续如何重新执行任务、选择新的 execution 并组装 Runtime，由宿主决定。
        配置的归档故障会降级后继续关闭，剩余缓冲此时可能未保存；其他异常传播。
        重复调用已关闭实例直接返回。
        """

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
        """结束本次执行：先提交归档尾部与业务结果，再关闭并尝试发布实时终态。

        设计意图：UI 收到结束通知时，宿主最终结果应已可读取。ArchiveStore.commit 必须在
        同一事务中保存尾部、终态和 completion，并拒绝不再允许写入的 execution。

        Args:
            status: 交给 codec.terminal 的业务终态；默认 JSON codec 接受 SUCCESS/FAILED，
                自定义 codec 可以使用自己的状态，例如 Counter 的 DONE。
            completion: 交给 ArchiveStore.commit 保存的最终业务结果，Runtime 不解释其结构。
            error_code: 只写入 after_close 的 Summary，便于统计；不自动加入终态或 completion。

        终态编码或 commit 失败时异常传播，pending 和未关闭状态保留；不发布实时终态或通知观察者。
        宿主须先确认提交结果，再决定是否重试，存储端负责不确定写入后的幂等。
        成功提交后，终态发布和观察者的 Exception 不改变已提交结果；再次 finish 抛 closed_error。
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
        """最终提交后的实时终态通知，独立于业务事件的实时预算。

        发布 Exception 只降低本实例完整性，不回滚结果，也不会再写一次已提交的归档。
        """

        try:
            self._publisher.publish(terminal_event)
        except Exception:
            # 此时宿主结果已提交；通知失败只能影响当前汇总，不能回滚或重复提交结果。
            self._degrade("UI stream终止事件实时发布失败", status=Integrity.DEGRADED)

    def _accept_business_event(self, *, payload_size: int) -> bool:
        """拒绝超大的单事件，并在条数阈值后停止归档缓冲。

        条数触顶仍返回 True，允许业务统计和实时流继续；单事件拒绝则不计入业务统计。
        """

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
        """超出实时字节预算后停止业务发布；配置内故障降级并返回原事件，其他异常传播。

        仅成功返回的发布计入实时字节预算，网络结果不确定时不会自动重发或去重。
        """

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
        """按归档编码字节约束 pending，并按条数、字节或活动时间触发 checkpoint。

        触顶时先尝试归档已有 pending；仍无容量才丢弃当前事件并停止后续缓冲。
        此处只吞配置的 archive_errors；完整持久归档的字节限制仍由存储端负责。
        """

        if self._archive_stopped:
            # 存储刚好满时此前可能没有损失；开始丢弃这条新事件才确定发生截断。
            if self._archive_status != Integrity.TRUNCATED:
                self._degrade("UI stream archive capacity exhausted", status=Integrity.TRUNCATED)
            self._touch_activity_if_due()
            return
        # 存储持续故障时 pending 无法清空，进程内缓冲仍须有界，不能依赖后端容量限制。
        if self._pending_bytes + payload_size > self.policy.max_archive_bytes:
            # 缓冲触顶可能只是归档后端短暂故障。先用现有 pending 做最后一次恢复性
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
        """把当前 pending 批次交给宿主，确认整批处理后才清空缓冲。

        存储端通过 Checkpoint 报告实际完整性和是否已无后续容量；调用抛错时缓冲仍保留。
        批次的事务、执行隔离和重试去重由存储端实现，Runtime 不生成事件去重键。
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
        # 容量标志单独不表示丢失；停止后续缓冲，实际丢弃事件时再标记截断。
        if result.capacity_exhausted:
            self._archive_stopped = True

    def _activity_checkpoint_due(self) -> bool:
        """仅在事件处理时检查活动间隔；没有后台定时线程，空闲时不会自行触发。"""

        return time.monotonic() - self._last_activity_checkpoint_at >= self.policy.activity_interval_seconds

    def _touch_activity_if_due(self) -> None:
        """停止归档后仍按事件到达节奏刷新活动；配置的归档故障继续按降级处理。"""

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
        """合并完整性并记日志；诊断只含绑定和计量信息，不写事件正文。"""

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
        """分别记录曾观察到的降级/截断，供关闭汇总使用，标记不会恢复为 False。"""

        if status == Integrity.DEGRADED:
            self._degraded = True
        elif status == Integrity.TRUNCATED:
            self._truncated = True

    def _require_open(self) -> None:
        """关闭后继续 send/finish 属于生命周期错误，抛出宿主配置的 closed_error。"""

        if self._closed:
            raise self._closed_error()

    @property
    def policy(self) -> StreamPolicy:
        """返回组装时的不可变阈值对象。"""

        return self._policy

    def _freeze_summary(self, *, status: str, error_code: str) -> Summary:
        """在锁内冻结当前计量和完整性，避免关闭回调再访问运行时状态。"""

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
        """在锁外调用可选观察者；其 Exception 只记日志，不向调用方传播。"""

        if self._after_close is None:
            return
        try:
            self._after_close(summary)
        except Exception:
            # 观察者失败不能改变结果，也不能让调用方把已提交执行当作失败重试。
            logger.warning("UI stream after-close notification failed")

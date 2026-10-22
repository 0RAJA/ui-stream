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
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Callable, Generic
from uuid import UUID

from ui_stream.archive import merge_archive_status
from ui_stream.exceptions import ArchiveUnavailable, ClosedStream, LiveUnavailable, ReentrantStreamOperation
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
_REPLACEMENT_REASON = "execution_replaced"


class StreamRuntime(Generic[EventT]):
    """协调一次任务执行的进度发送、分批归档和最终结果提交。

    使用方（宿主应用）先创建 execution 并组装三个端口，再反复 send，最后调用 finish。
    Runtime 把暂未归档的事件保存在 pending 缓冲中，达到阈值后交给 ArchiveStore。
    policy 可为固定值或无参工厂；每次 send 共用一份策略，配置变化在下次发送时生效。
    实例内 RLock 覆盖端口 I/O，保证同一实例按取得锁的顺序处理；同线程回调可读取属性。
    实例仍打开时禁止写操作重入；端口不得阻塞等待另一线程调用需同一锁的方法或属性，
    包括 pending_count、closed、archive_status，否则可能死锁。跨实例/进程隔离由存储端校验。
    成功 finish 或 close_for_retry 后，这个实例不再接受写入。
    """

    def __init__(
        self,
        *,
        binding: ExecutionBinding,
        publisher: LivePublisher[EventT],
        codec: EventCodec[EventT],
        archive_store: ArchiveStore[EventT],
        archive_status: Integrity = Integrity.COMPLETE,
        policy: StreamPolicy | Callable[[], StreamPolicy] = StreamPolicy(),
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
            policy: 固定 StreamPolicy 或返回它的无参工厂；构造时不执行工厂。
                工厂只计算或读取配置，不应重入本实例的 send/finish/close_for_retry。
            after_close: 关闭后接收 Summary 的可选观察者，在释放实例锁后调用。
            archive_errors: 中间归档时允许降级继续的异常类型；最终 commit 异常仍传播。
            live_errors: 业务实时发布时允许降级继续的异常类型。实例仍打开时的写重入
                抛 ReentrantStreamOperation，不受这两个后端异常配置影响；提交后的
                终态通知另按 Exception 处理，不改变已提交结果。
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
        self._write_in_progress = False
        self._possible_side_effect = False
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
        """是否已在正常 finish 或 close_for_retry 后关闭；最终 commit 抛错时保持未关闭。"""

        with self._lock:
            return self._closed

    def send(self, data: Any) -> EventT | None:
        """处理一条业务进度，先校验与发布，再缓冲并按阈值归档。

        一次 send 共用一份 policy，并可能同步执行端口 I/O。可恢复发布/归档故障只降级；
        返回事件不代表送达或持久保存。归档编码在发布后失败时，已经发生的实时写入不回滚。

        Args:
            data: 交给 codec.business 的输入，结构和合法性由 codec 决定。

        Returns:
            后续归档使用的事件，可能带发布回执；单条 LIVE 编码超限时返回 None。
            条数触顶或实时降级时仍可返回事件，即使它未被发布或加入归档缓冲。

        Raises:
            ClosedStream: 实例已关闭时的默认异常，可由 closed_error 替换。
            ReentrantStreamOperation: 实例仍打开时，端口或策略工厂重入同一实例的写操作。
                若本次已进入可能写入的端口则同时记 DEGRADED，表示完整性不确定而非确认丢失。
            TypeError: 策略工厂未返回 StreamPolicy。
            Exception: codec 构造/编码错误或未配置为可恢复的端口错误原样传播。
                默认 codec 的非法输入使用 InvalidEvent；fencing 通常使用 StaleExecution。
        """

        with self._lock:
            self._require_open()
            with self._write_operation():
                # 本次发送共用同一策略，发布或归档期间配置变化只影响下一次 send。
                policy = self.policy
                ui_event = self._codec.business(data)
                # 先编码：非法 data 属于接入错误，必须在任何降级判断前暴露。
                live_payload_size = len(self._codec.encode(ui_event, EncodingTarget.LIVE))
                if not self._accept_business_event(payload_size=live_payload_size, policy=policy):
                    return None
                self._business_event_count += 1
                self._business_event_bytes += live_payload_size
                ui_event = self._write_live(ui_event, payload_size=live_payload_size, policy=policy)
                # publisher 可返回带回执的副本；按发布后的 ARCHIVE 编码计量，避免漏算回执占用。
                archive_payload_size = len(self._codec.encode(ui_event, EncodingTarget.ARCHIVE))
                self._buffer(ui_event, payload_size=archive_payload_size, policy=policy)
                return ui_event

    def announce_replacement(self, previous_publisher: LivePublisher[EventT]) -> bool:
        """向旧实时流发送切换提示，传输故障只降级这个新 Runtime。

        宿主先提交 execution 轮换和旧写者隔离；本方法不轮换、不归档、不计预算，也不重试。
        只有发布阶段的 live_errors 返回 False 并记降级；日志只含新绑定与异常类型。

        Args:
            previous_publisher: 绑定旧执行通道的发布端，须接受新 codec 构造的同一事件协议。

        Returns:
            发布端正常返回时为 True，不保证旧读者收到；配置内传输故障时为 False。

        Raises:
            ClosedStream: 实例已关闭时的默认异常，可由 closed_error 替换。
            ReentrantStreamOperation: 实例仍打开时写操作发生重入，原样传播；
                若已进入写端口则标记 DEGRADED，不转换为 False。
            Exception: reset 构造、LIVE 编码及未配置为可恢复的发布异常原样传播。
        """

        with self._lock:
            self._require_open()
            with self._write_operation():
                event = self._codec.reset(_REPLACEMENT_REASON)
                self._codec.encode(event, EncodingTarget.LIVE)
                try:
                    self._possible_side_effect = True
                    previous_publisher.publish(event)
                except ReentrantStreamOperation:
                    raise
                except self._live_errors as error:
                    self._degrade(
                        "UI stream replacement announcement unavailable",
                        status=Integrity.DEGRADED,
                        extra={"operation": "announce_replacement", "error_type": type(error).__name__},
                    )
                    return False
                return True

    def close_for_retry(self) -> None:
        """为宿主重新执行任务收场，尝试 checkpoint 后关闭本 Runtime。

        不构造终态、不调用 commit，也不保存最终业务结果。配置内归档故障会降级后继续关闭，
        所以正常返回不是持久化成功回执；剩余 pending 可能未保存。已关闭时重复调用直接返回。
        任务调度、执行轮换和是否重跑计算由宿主决定。

        Returns:
            None。只表示本次关闭流程已完成，不确认任务已被调度或进度已持久化。

        Raises:
            ReentrantStreamOperation: 实例仍打开时，同一实例的写操作发生重入。
                若本次已进入可能写入的端口则同时记 DEGRADED，表示完整性不确定而非确认丢失。
            Exception: 未配置为可恢复的 checkpoint 异常原样传播，此时保持未关闭。
                默认配置下 StaleExecution 不会被当作临时归档故障吞掉。
        """

        with self._lock:
            if self._closed:
                return
            with self._write_operation():
                try:
                    self._checkpoint()
                except ReentrantStreamOperation:
                    raise
                except self._archive_errors:
                    self._degrade("UI stream checkpoint before retry close unavailable", status=Integrity.DEGRADED)
                self._closed = True
                summary = self._freeze_summary(status="RETRY", error_code="")
        self._notify_closed(summary)

    def finish(self, *, status: str, completion: Any, error_code: str = "") -> None:
        """先提交归档尾部与业务结果，再关闭并尝试发送实时终态。

        ArchiveStore.commit 应在同一事务内保存尾部、终态及 completion，并校验 execution。
        编码或 commit 失败时保留 pending 和打开状态；宿主先确认提交结果，再决定是否重试。
        成功提交后的终态发布和 after_close 的 Exception 只降级或记录日志，不撤销提交。

        Args:
            status: 交给 codec.terminal 的业务终态；默认 JSON 使用 SUCCESS/FAILED，
                自定义 codec 可使用自己的词汇，例如 Counter 的 DONE。
            completion: 交给 commit 保存的最终业务结果，Runtime 不解释其结构。
            error_code: 仅进入关闭 Summary，不自动写入终态或 completion。

        Returns:
            None。正常返回表示 commit 已按存储端契约成功，不保证实时终态已送达。

        Raises:
            ClosedStream: 实例已关闭时的默认异常，可由 closed_error 替换。
            ReentrantStreamOperation: 实例仍打开时，提交前的端口或 codec 重入同一实例写操作。
                若本次已进入可能写入的端口则同时记 DEGRADED，表示完整性不确定而非确认丢失。
            Exception: 终态构造/编码及 commit 的异常原样传播；commit 的 ArchiveUnavailable
                也不会降级吞掉。超时后的写入结果可能不确定，幂等由存储端保证。
        """

        with self._lock:
            self._require_open()
            with self._write_operation():
                terminal_event = self._codec.terminal(status)
                self._codec.encode(terminal_event, EncodingTarget.ARCHIVE)
                self._codec.encode(terminal_event, EncodingTarget.LIVE)
                self._possible_side_effect = True
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

        此时实例已关闭；通知回调再次 send 会先抛 closed_error，同样作为通知 Exception
        降级处理。它不向外层 finish 传播，也不回滚或重复提交已经保存的结果。
        """

        try:
            self._publisher.publish(terminal_event)
        except Exception:
            # 此时宿主结果已提交；通知失败只能影响当前汇总，不能回滚或重复提交结果。
            self._degrade("UI stream terminal publication unavailable", status=Integrity.DEGRADED)

    def _accept_business_event(self, *, payload_size: int, policy: StreamPolicy) -> bool:
        """拒绝超大的单事件，并在条数阈值后停止归档缓冲。

        条数触顶仍返回 True，允许业务统计和实时流继续；单事件拒绝则不计入业务统计。
        """

        if payload_size > policy.max_event_bytes:
            self._degrade(
                "UI stream event exceeds byte limit",
                status=Integrity.TRUNCATED,
                extra={"payload_size": payload_size},
            )
            return False
        if self._business_event_count >= policy.max_events:
            if not self._archive_stopped:
                self._archive_stopped = True
                self._degrade(
                    "UI stream event count limit reached",
                    status=Integrity.TRUNCATED,
                )
        return True

    def _write_live(self, event: EventT, *, payload_size: int, policy: StreamPolicy) -> EventT:
        """超出实时字节预算后停止业务发布；配置内故障降级并返回原事件，其他异常传播。

        仅成功返回的发布计入实时字节预算，网络结果不确定时不会自动重发或去重。
        因此该预算不是实际网络总字节的硬上限，也不约束 publisher 内部重试。
        """

        if self._live_stopped:
            return event
        if self._live_business_bytes + payload_size > policy.max_live_bytes:
            self._live_stopped = True
            self._degrade("UI stream live byte budget exhausted", status=Integrity.DEGRADED)
            return event
        try:
            self._possible_side_effect = True
            published = self._publisher.publish(event)
        except ReentrantStreamOperation:
            raise
        except self._live_errors:
            self._degrade("UI stream live publication unavailable", status=Integrity.DEGRADED)
            return event
        self._live_business_bytes += payload_size
        return published

    def _buffer(self, event: EventT, *, payload_size: int, policy: StreamPolicy) -> None:
        """按归档编码字节约束 pending，并按条数、字节或活动时间触发 checkpoint。

        触顶时先尝试归档已有 pending；仍无容量才丢弃当前事件并停止后续缓冲。
        此处只吞配置的 archive_errors；完整持久归档的字节限制仍由存储端负责。
        """

        if self._archive_stopped:
            # 存储刚好满时此前可能没有损失；开始丢弃这条新事件才确定发生截断。
            if self._archive_status != Integrity.TRUNCATED:
                self._degrade("UI stream archive capacity exhausted", status=Integrity.TRUNCATED)
            self._touch_activity_if_due(policy=policy)
            return
        # 存储持续故障时 pending 无法清空，进程内缓冲仍须有界，不能依赖后端容量限制。
        if self._pending_bytes + payload_size > policy.max_archive_bytes:
            # 缓冲触顶可能只是归档后端短暂故障。先用现有 pending 做最后一次恢复性
            # checkpoint；成功后继续接收当前事件，失败或持久归档已耗尽才截断。
            try:
                self._checkpoint()
            except ReentrantStreamOperation:
                raise
            except self._archive_errors:
                self._degrade(
                    "UI stream checkpoint before capacity limit unavailable",
                    status=Integrity.DEGRADED,
                )
        if self._archive_stopped or self._pending_bytes + payload_size > policy.max_archive_bytes:
            self._archive_stopped = True
            self._degrade(
                "UI stream pending byte capacity exhausted",
                status=Integrity.TRUNCATED,
            )
            self._touch_activity_if_due(policy=policy)
            return
        self._pending.append(event)
        self._pending_bytes += payload_size
        if (
            len(self._pending) >= policy.checkpoint_event_count
            or self._pending_bytes >= policy.checkpoint_bytes
            or self._activity_checkpoint_due(policy=policy)
        ):
            try:
                self._checkpoint()
            except ReentrantStreamOperation:
                raise
            except self._archive_errors:
                self._degrade("UI stream automatic checkpoint unavailable", status=Integrity.DEGRADED)

    def _checkpoint(self) -> None:
        """把当前 pending 批次交给宿主，确认整批处理后才清空缓冲。

        存储端通过 Checkpoint 报告实际完整性和是否已无后续容量；调用抛错时缓冲仍保留。
        批次的事务、执行隔离和重试去重由存储端实现，Runtime 不生成事件去重键。
        """

        if not self._pending:
            return
        self._possible_side_effect = True
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

    def _activity_checkpoint_due(self, *, policy: StreamPolicy) -> bool:
        """仅在事件处理时检查活动间隔；没有后台定时线程，空闲时不会自行触发。"""

        return time.monotonic() - self._last_activity_checkpoint_at >= policy.activity_interval_seconds

    def _touch_activity_if_due(self, *, policy: StreamPolicy) -> None:
        """停止归档后仍按事件到达节奏刷新活动；配置的归档故障继续按降级处理。"""

        if not self._activity_checkpoint_due(policy=policy):
            return
        try:
            self._possible_side_effect = True
            self._archive_store.touch_activity(binding=self._binding)
        except ReentrantStreamOperation:
            raise
        except self._archive_errors:
            self._degrade("UI stream activity refresh unavailable", status=Integrity.DEGRADED)
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

    @contextmanager
    def _write_operation(self) -> Iterator[None]:
        """实例仍打开且持锁时拒绝嵌套写入，同线程属性读取仍可复用 RLock。

        端口回调重入会打乱发布顺序或让外层清空新加入的 pending，不能仅依赖可重入锁。
        只有重入异常逃出外层且已进入写端口时才降级；含最终 commit，但不证明数据已丢失。
        标志每次操作独立，并在 finally 释放；宿主自行捕获内层拒绝不会影响成功的外层调用。
        """

        if self._write_in_progress:
            raise ReentrantStreamOperation("Runtime write operations must not reenter the same instance")
        self._write_in_progress = True
        self._possible_side_effect = False
        try:
            yield
        except ReentrantStreamOperation:
            if self._possible_side_effect:
                self._degrade("UI stream reentrant operation interrupted a possible write", status=Integrity.DEGRADED)
            raise
        finally:
            self._possible_side_effect = False
            self._write_in_progress = False

    def _require_open(self) -> None:
        """关闭后继续写入抛 closed_error；该检查先于写重入检测。

        因此提交后的终态通知回调 send/finish/announce_replacement 先得到关闭异常。
        """

        if self._closed:
            raise self._closed_error()

    @property
    def policy(self) -> StreamPolicy:
        """返回当前策略；固定对象原样返回，工厂在每次读取时求值一次。

        此属性保持只读；工厂返回非 StreamPolicy 时抛 TypeError，其自身异常直接传播。
        """

        policy = self._policy
        if callable(policy) and not isinstance(policy, StreamPolicy):
            policy = policy()
            if not isinstance(policy, StreamPolicy):
                raise TypeError("policy factory must return StreamPolicy")
        return policy

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

# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""定义单次 execution 的数据契约与可替换端口，不绑定业务事件或后端。

Runtime 经 codec 构造/计量事件，再交给 publisher 与 ArchiveStore。
宿主负责身份绑定、fencing 与持久化事务；事件接收后不得再被调用方或适配器修改。
这些 Protocol 描述调用约定，不会自动实现事务、深拷贝或跨实例并发控制。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Generic, Protocol, TypeVar
from uuid import UUID

EventT = TypeVar("EventT")
_ArchiveEventT = TypeVar("_ArchiveEventT", contravariant=True)


class Integrity(StrEnum):
    """描述一次执行的 UI 进度历史是否可能缺失，与业务成功/失败状态分开。

    实时通道或中间归档故障记为 DEGRADED；已知丢弃事件记为 TRUNCATED。
    后续恢复正常也保留已知缺口，因此合并只沿 COMPLETE → DEGRADED → TRUNCATED 升级。
    COMPLETE 表示尚未发现这些问题，不表示 Redis 永久保留历史或事件恰好投递一次。
    """

    COMPLETE = "COMPLETE"  # 尚未发现历史缺口或后端降级。
    DEGRADED = "DEGRADED"  # 实时/中间归档出现故障或预算停止，未必已有事件被丢弃。
    TRUNCATED = "TRUNCATED"  # 已知有事件因大小、条数、缓冲或持久容量被丢弃。


class EncodingTarget(StrEnum):
    """告诉 codec 此次编码用于实时发布还是归档，以计入不同表示的真实大小。

    例如 Redis 发布前尚无 stream_id，发布后归档则可保存这个回执。
    Runtime 用编码字节校验和计量，传给 publisher/store 的仍是原类型事件对象。
    """

    LIVE = "LIVE"  # 发布前校验与实时业务字节预算；默认 JSON 省略回执。
    ARCHIVE = "ARCHIVE"  # 发布后归档缓冲计量；默认 JSON 保留 stream_id。


class ExecutionBinding(Protocol):
    """约定一次写入属于哪个资源、任务和 execution，由宿主提供这组身份。

    execution 表示任务的一次具体执行；宿主重试或替换执行时可分配新的 execution_id。
    存储端据此拒绝旧执行继续写入（fencing），避免它覆盖新结果；只读属性本身不执行校验。
    """

    @property
    def resource_id(self) -> str:
        """归档或结果所属资源的宿主标识。"""

        ...

    @property
    def task_id(self) -> str:
        """宿主任务标识，用于关联执行及诊断信息。"""

        ...

    @property
    def execution_id(self) -> UUID:
        """当前这次执行的唯一标识，用于区分同一任务的重试或替换执行。"""

        ...


@dataclass(frozen=True, slots=True)
class Binding:
    """保存资源、任务和执行 ID 的不可变对象，可直接用作 ExecutionBinding。

    宿主负责创建、校验这些身份，并在重试或执行切换时决定如何更换 execution_id。
    """

    resource_id: str  # 资源归属，不是实时 key 的生成规则。
    task_id: str  # 业务任务标识，由宿主解释。
    execution_id: UUID  # 当前执行代际，交给存储端用于 fencing。


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """ArchiveStore.checkpoint 对整批进度已处理完毕的回执。

    Runtime 收到回执后清空本批 pending；宿主应把丢弃或后端降级反映在 archive_status 中。
    capacity_exhausted 表示以后不应再缓冲新事件；本批恰好填满且未丢事件时仍可 COMPLETE，
    下一条实际丢弃时 Runtime 才升级为 TRUNCATED。
    """

    archive_status: Integrity
    capacity_exhausted: bool = False  # 停止后续缓冲，不单独表示本批已有损失。


@dataclass(frozen=True, slots=True)
class ReadBatch(Generic[EventT]):
    """一次实时读取的有效事件，以及最后扫描到的后端原始游标。

    例如扫描到三个 Redis entry，第三条 payload 损坏：events 只有前两条，
    last_seen_stream_id 仍指向第三条，避免下次反复扫描它；各有效事件保留自身游标。
    没有读到任何原始 entry 时，events 为空且 last_seen_stream_id 为 None。
    """

    events: list[EventT]  # 已解析成功、保持后端顺序的事件。
    last_seen_stream_id: str | None  # 最后扫描的原始 ID，允许指向被跳过的脏 entry。


@dataclass(frozen=True, slots=True)
class StreamPolicy:
    """配置业务进度的容量限制、归档批量和活动刷新间隔。

    send 按这些阈值决定拒绝单条事件、停止实时业务发布、停止归档缓冲或执行 checkpoint。
    LIVE/ARCHIVE 字节分别由 codec 计量；max_archive_bytes 只限制本实例 pending 的字节和，
    完整持久归档还需存储端自行限额。时间阈值由事件到达触发，不会启动后台定时器。
    终态另走 commit 和实时通知；这个值对象不校验配置参数。
    """

    max_event_bytes: int = 256 * 1024  # 单个业务事件的 LIVE 编码上限，超限拒绝该事件。
    max_events: int = 10_000  # 业务条数触顶后停止归档缓冲，实时发送仍可继续。
    max_live_bytes: int = 10 * 1024 * 1024  # 成功发布的业务 LIVE 编码累计预算。
    max_archive_bytes: int = 10 * 1024 * 1024  # 本实例 pending 编码字节和，不含存储信封开销。
    activity_interval_seconds: int = 60  # 事件到达时检查的活动刷新间隔。
    checkpoint_event_count: int = 20  # pending 条数达到阈值即尝试 checkpoint。
    checkpoint_bytes: int = 256 * 1024  # pending 的 ARCHIVE 编码字节阈值。


@dataclass(frozen=True, slots=True)
class Summary:
    """传给 after_close 的不可变关闭摘要，便于记录计量与降级指标。

    Runtime 关闭时冻结摘要，再在锁外通知观察者，避免观察者读取事件正文或依赖运行时对象。
    degraded/truncated 分别表示曾观察到的情况，可以同时为真；error_code 是宿主的统计标签。
    event_count/event_bytes 在单条大小校验后、发布前计入，不等于成功发布或归档的数量。
    """

    status: str  # finish 的领域状态，或 finish_retry 使用的 RETRY。
    error_code: str  # 宿主提供的通知标签，不自动注入终态或 completion。
    archive_status: Integrity  # 关闭时的内存完整性，包含终态实时通知的降级。
    degraded: bool  # 是否曾观察到 DEGRADED，可与 truncated 同时为真。
    truncated: bool  # 是否曾观察到 TRUNCATED。
    event_count: int  # 计入业务统计的条数，不含终态与单条超限的拒绝事件。
    event_bytes: int  # 上述业务事件的 LIVE 编码字节和。


AfterClose = Callable[[Summary], None]


class EventCodec(Protocol[EventT]):
    """把宿主业务输入转换为事件，并提供实时与归档两种编码。

    Runtime 调用 business(data) 处理进度，调用 terminal(status) 构造结束事件，再用 encode
    校验和计量。实现这些方法即可替换默认 JSON 模型，不必继承 Event。
    构造与编码都不执行后端 I/O，也不修改已接受的事件；编码错误直接交给调用方处理。
    """

    def business(self, data: Any) -> EventT:
        """将宿主值构造成业务事件；后续编码仍须验证其可接受性。"""

        ...

    def terminal(self, status: str) -> EventT:
        """按宿主领域状态构造终态；支持哪些状态由 codec 决定。"""

        ...

    def encode(self, event: EventT, target: EncodingTarget) -> bytes:
        """返回事件在目标通道中的编码字节，供 Runtime 校验和计量。

        event 是 business/terminal 或 publisher 返回的事件；target 指定 LIVE 或 ARCHIVE。
        编码应与对应端口的实际表示一致，包含该目标需要的回执等字段，不发送或存储事件。
        """

        ...


class LivePublisher(Protocol[EventT]):
    """将事件同步写到实时通道，返回后续用于归档的事件对象。

    一般原样返回；若后端生成消息 ID，可像 Redis 适配器一样返回附加回执的新事件。
    成功返回前应完成发布，不修改传入事件；超时后是否已写入取决于后端，核心不自动重发或去重。
    """

    def publish(self, event: EventT) -> EventT:
        """发布一次并返回后续用于归档的事件；可恢复异常由 Runtime 的 live_errors 配置。"""

        ...


class ArchiveStore(Protocol[_ArchiveEventT]):
    """宿主应用实现的进度归档与最终结果存储端口。

    checkpoint 分批保存进度，commit 在任务结束时原子保存尾部、终态和最终结果。
    宿主可保存最新值、累计快照或有界事件列表；成功返回表示整个批次已处理，丢弃须报告完整性。
    实现方负责事务与执行身份校验，防止旧 execution 覆盖新结果，也负责不确定写入后的重试幂等。
    """

    def checkpoint(
        self, *, binding: ExecutionBinding, events: tuple[_ArchiveEventT, ...], archive_status: Integrity
    ) -> Checkpoint:
        """处理一批待归档事件，返回其完整性与后续容量状态。

        Args:
            binding: 本批所属执行，存储端应确认它仍允许写入。
            events: Runtime 自上次成功归档后积累的有序事件；可以据此更新快照或追加历史。
            archive_status: Runtime 当前已知的归档状态，存储端应与已有状态合并。

        Returns:
            Checkpoint 回执。Runtime 随后会清空整批 pending，因此不能静默漏存一部分；
            因容量丢弃的事件应反映为 TRUNCATED。操作失败应抛异常，使缓冲保留。
        """

        ...

    def touch_activity(self, *, binding: ExecutionBinding) -> None:
        """更新本次执行的活动时间，供宿主判断任务是否仍有进展。

        Runtime 停止归档后，在新事件到达且时间阈值到期时调用；不是独立后台心跳。
        存储端仍应校验 binding，防止旧执行刷新新执行的活动时间。
        """

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
        """结束执行时，在一个宿主事务内保存归档尾部、终态和最终业务结果。

        Args:
            binding: 本次执行的身份，应在事务内确认仍允许写入。
            events: 尚未成功 checkpoint 的有序业务事件，可能为空。
            terminal_event: codec.terminal 构造的结束事件。
            archive_status: Runtime 当前已知的完整性，须与存储中已有状态合并。
            completion: 宿主定义的最终结果，由本方法解释和保存。

        Returns:
            本次持久化后的 Integrity。只有事务真正提交后才能返回；失败应抛异常，
            Runtime 才会保留 pending 和未关闭状态，而不发布结束通知。
        """

        ...

"""最小宿主示例：发送两个计数值，保存最新值，再提交求和结果。

make_counter 组装自定义事件、文本编码、内存发布器和快照存储，展示三个端口如何协作。
运行 python -m examples.counter 查看帧和快照；无需 Django、Redis 或 Pydantic。
数据仅保存在内存中，进程退出即丢失；持久化事务与任务调度应由实际宿主提供。
"""

import threading
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from ui_stream import StreamPolicy, StreamRuntime
from ui_stream.exceptions import InvalidEvent, StaleExecution
from ui_stream.types import AfterClose, Binding, Checkpoint, EncodingTarget, Integrity


@dataclass(frozen=True, slots=True)
class CounterEvent:
    kind: str
    value: int | str
    receipt: int | None = None


class CounterCodec:
    """把非负整数变成 tick 事件，把 DONE/FAILED 变成 done 事件。

    使用简单 ASCII 文本计量，说明事件不必继承默认 JSON Event，也不必采用 SUCCESS 终态。
    """

    def business(self, data: Any) -> CounterEvent:
        if type(data) is not int or data < 0:
            raise InvalidEvent("Counter requires a non-negative integer")
        return CounterEvent("tick", data)

    def terminal(self, status: str) -> CounterEvent:
        if status not in ("DONE", "FAILED"):
            raise InvalidEvent("Unsupported counter terminal")
        return CounterEvent("done", status)

    def encode(self, event: CounterEvent, target: EncodingTarget) -> bytes:
        frame = f"{event.kind}:{event.value}"
        if target == EncodingTarget.ARCHIVE:
            frame += f"@{event.receipt}"
        return frame.encode("ascii")


@dataclass(frozen=True, slots=True)
class CounterSnapshot:
    latest: int | None = None
    terminal: CounterEvent | None = None
    result: int | None = None
    integrity: Integrity = Integrity.COMPLETE


class MemorySnapshotStore:
    """用一次不可变对象替换更新最新值、终态和结果，展示 ArchiveStore 契约。

    实例锁保护内存更新，绑定校验拒绝其他执行和已结束后的写入；这不是跨进程持久存储。
    """

    def __init__(self, binding: Binding):
        self._binding = binding
        self._lock = threading.RLock()
        self.snapshot = CounterSnapshot()

    def _require_current(self, binding):
        if binding != self._binding or self.snapshot.terminal is not None:
            raise StaleExecution()

    def _next(self, events, archive_status):
        latest = self.snapshot.latest
        for event in events:
            latest = event.value
        return replace(self.snapshot, latest=latest, integrity=archive_status)

    def checkpoint(self, *, binding, events, archive_status):
        with self._lock:
            self._require_current(binding)
            self.snapshot = self._next(events, archive_status)
            return Checkpoint(archive_status=archive_status)

    def touch_activity(self, *, binding):
        with self._lock:
            self._require_current(binding)

    def commit(self, *, binding, events, terminal_event, archive_status, completion):
        with self._lock:
            self._require_current(binding)
            snapshot = self._next(events, archive_status)
            # Tail, terminal and result become visible in one state replacement.
            self.snapshot = replace(snapshot, terminal=terminal_event, result=completion)
            return archive_status


class MemoryPublisher:
    """记录实时文本帧，并为每次发布返回递增回执。

    遇到 done 时同时记录存储中可读的最终结果，用于验证提交先于结束通知。
    """

    def __init__(self, codec: CounterCodec, store: MemorySnapshotStore):
        self._codec = codec
        self._store = store
        self.frames: list[bytes] = []
        self.results_at_terminal: list[int | None] = []

    def publish(self, event: CounterEvent) -> CounterEvent:
        self.frames.append(self._codec.encode(event, EncodingTarget.LIVE))
        if event.kind == "done":
            self.results_at_terminal.append(self._store.snapshot.result)
        return replace(event, receipt=len(self.frames))


def counter_binding() -> Binding:
    return Binding(resource_id="counter:example", task_id="counter-task", execution_id=uuid4())


def make_counter(*, policy: StreamPolicy = StreamPolicy(), after_close: AfterClose | None = None):
    """组装独立内存 Counter，返回 (runtime, publisher, store)。

    可传入 StreamPolicy 调整归档阈值，或用 after_close 观察结束摘要。
    随后调用 runtime.send(非负整数)，最后 finish(status="DONE", completion=求和结果)。
    """

    binding = counter_binding()
    codec = CounterCodec()
    store = MemorySnapshotStore(binding)
    publisher = MemoryPublisher(codec, store)
    runtime = StreamRuntime(
        binding=binding, publisher=publisher, codec=codec, archive_store=store, policy=policy, after_close=after_close
    )
    return runtime, publisher, store


if __name__ == "__main__":
    runtime, channel, snapshots = make_counter()
    runtime.send(4)
    runtime.send(9)
    runtime.finish(status="DONE", completion=13)
    print(channel.frames)
    print(snapshots.snapshot)

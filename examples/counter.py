"""A counter consumer using domain dataclasses, text frames and a latest-value snapshot.

Run ``python -m examples.counter`` without Django, Redis or Pydantic. This
in-process demonstration has no restart durability; a real host supplies its
own transaction and execution ownership.
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
    """Independent domain vocabulary and non-JSON encoding; no default event inheritance."""

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
    """Replace one immutable snapshot under a lock; only the bound execution may write."""

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
    """An in-process text channel with transport receipts and terminal result visibility."""

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

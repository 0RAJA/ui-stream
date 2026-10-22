"""公共流组件必须在无宿主框架初始化的普通 Python 进程中完成生命周期。"""

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

BOOTSTRAP = """
import sys
from uuid import uuid4
from ui_stream import Integrity, StreamPolicy, StreamRuntime
from ui_stream.codec import Event, JsonEventCodec
from ui_stream.types import Binding, Checkpoint, ReadBatch
from ui_stream.exceptions import ArchiveUnavailable, ClosedStream, LiveUnavailable
from ui_stream.sse import encode_sse_event

class Live:
    def __init__(self):
        self.events = []
        self.fail = False
        self.terminal_results = []
    def publish(self, event):
        if self.fail:
            raise LiveUnavailable()
        if event.event == "platform.stream_end":
            self.terminal_results.append(archive.result)
        result = event.model_copy(update={"stream_id": f"1-{len(self.events)}"})
        self.events.append(result)
        return result

class Archive:
    def __init__(self):
        self.events = []
        self.result = None
        self.fail_checkpoint = False
        self.fail_commit = False
    def checkpoint(self, *, binding, events, archive_status):
        if self.fail_checkpoint:
            raise ArchiveUnavailable()
        self.events.extend(events)
        return Checkpoint(archive_status=archive_status)
    def touch_activity(self, *, binding):
        pass
    def commit(self, *, binding, events, terminal_event, archive_status, completion):
        if self.fail_commit:
            raise ArchiveUnavailable()
        self.events.extend(events)
        self.events.append(terminal_event)
        self.result = completion
        return archive_status

live = Live()
archive = Archive()
binding = Binding(resource_id="job:example", task_id="task", execution_id=uuid4())
runtime = StreamRuntime(binding=binding, publisher=live, codec=JsonEventCodec(),
                        archive_store=archive, policy=StreamPolicy())
"""


class StandaloneStreamContractTest(unittest.TestCase):
    def run_contract(self, source, *, bootstrap=BOOTSTRAP):
        root = os.environ.get("UI_STREAM_TEST_PATH", str(Path(__file__).resolve().parents[1] / "src"))
        script = (
            "import sys; sys.path[:0] = "
            + repr([root, str(Path(__file__).resolve().parents[1])])
            + "\n"
            + bootstrap
            + textwrap.dedent(source)
        )
        result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_generic_job_commits_tail_and_result_before_terminal_publish(self):
        self.run_contract(
            """
            event = runtime.send({"progress": 50})
            assert event.stream_id == "1-0"
            assert runtime.execution_id == binding.execution_id
            assert archive.events == []
            runtime.finish(status="SUCCESS", completion={"file": "generated.csv"})
            assert archive.result == {"file": "generated.csv"}
            assert [e.data for e in archive.events] == [{"progress": 50}, {"status": "SUCCESS"}]
            assert archive.events[-1].stream_id is None
            assert live.events[-1].event == "platform.stream_end"
            assert live.terminal_results == [{"file": "generated.csv"}]
            assert runtime.closed
            forbidden = ("django", "services", "rest_framework", "celery")
            assert not any(name.split(".")[0] in forbidden for name in sys.modules)
        """
        )

    def test_live_failure_keeps_snapshot_and_successful_result(self):
        self.run_contract(
            """
            live.fail = True
            event = runtime.send({"progress": 50})
            assert event.stream_id is None
            runtime.finish(status="SUCCESS", completion="result")
            assert archive.result == "result"
            assert archive.events[0].data == {"progress": 50}
            assert runtime.archive_status == Integrity.DEGRADED
        """
        )

    def test_commit_failure_keeps_pending_open_and_never_publishes_end(self):
        self.run_contract(
            """
            summaries = []
            runtime = StreamRuntime(binding=binding, publisher=live, codec=JsonEventCodec(),
                                    archive_store=archive, after_close=summaries.append)
            runtime.send({"progress": 50})
            archive.fail_commit = True
            try:
                runtime.finish(status="SUCCESS", completion="result")
            except ArchiveUnavailable:
                pass
            else:
                raise AssertionError("commit error swallowed")
            assert archive.result is None and summaries == []
            assert runtime.pending_count == 1 and not runtime.closed
            assert all(e.event is None for e in live.events)
            archive.fail_commit = False
            runtime.finish(status="SUCCESS", completion="result")
            assert len(archive.events) == 2
        """
        )

    def test_retry_flushes_without_terminal_and_closes_runtime(self):
        self.run_contract(
            """
            runtime.send({"progress": 50})
            runtime.close_for_retry()
            assert len(archive.events) == 1 and archive.result is None
            assert len(live.events) == 1 and runtime.closed
            try:
                runtime.send({"progress": 100})
            except ClosedStream:
                pass
            else:
                raise AssertionError("closed runtime accepted event")
        """
        )

    def test_pending_capacity_is_bounded_during_archive_outage(self):
        self.run_contract(
            """
            runtime = StreamRuntime(binding=binding, publisher=live, codec=JsonEventCodec(), archive_store=archive,
                                    policy=StreamPolicy(max_archive_bytes=120, checkpoint_event_count=1))
            archive.fail_checkpoint = True
            for _ in range(20):
                runtime.send({"delta": "x" * 20})
            assert runtime._pending_bytes <= 120
            assert runtime.archive_status == Integrity.TRUNCATED
        """
        )

    def test_wire_and_strict_json_are_independent_of_host(self):
        self.run_contract(
            """
            from ui_stream.exceptions import InvalidEvent
            encoded = encode_sse_event(Event(data={"delta": "line\\nnext"}, stream_id="1-0"))
            assert encoded.startswith(b"id: 1-0\\ndata: ")
            try:
                runtime.send({"value": float("nan")})
            except InvalidEvent:
                pass
            else:
                raise AssertionError("non-standard JSON accepted")
        """
        )

    def test_redis_adapter_normalizes_network_failure(self):
        self.run_contract(
            """
            from redis.exceptions import RedisError
            from ui_stream.redis import BoundRedisPublisher, RedisLiveStore
            class BrokenClient:
                def register_script(self, script):
                    def broken(**kwargs):
                        raise RedisError("backend down")
                    return broken
            adapter = RedisLiveStore(client=BrokenClient())
            runtime = StreamRuntime(binding=binding, publisher=BoundRedisPublisher(adapter, "test:job"),
                                    codec=JsonEventCodec(), archive_store=archive)
            runtime.send({"progress": 50})
            runtime.finish(status="SUCCESS", completion="result")
            assert archive.result == "result" and runtime.archive_status == Integrity.DEGRADED
        """
        )

    def test_reader_advances_dirty_cursor_and_closes_at_end(self):
        self.run_contract(
            """
            from ui_stream.reader import iter_live
            class Reader:
                def __init__(self):
                    self.cursors = []
                def read(self, *, redis_key, after_id, block_ms):
                    self.cursors.append(after_id)
                    if len(self.cursors) == 1:
                        return ReadBatch(events=[], last_seen_stream_id="1-2")
                    return ReadBatch(events=[Event(event="platform.stream_end", data={"status":"SUCCESS"})],
                                     last_seen_stream_id="1-3")
            reader = Reader()
            events = list(iter_live(redis_store=reader, redis_key="test:job", cursor="0-0", clock=lambda: 0))
            assert events[0] is None and events[1].event == "platform.stream_end"
            assert reader.cursors == ["0-0", "1-2"]
        """
        )

    def test_unsupported_terminal_does_not_commit_or_publish(self):
        self.run_contract(
            """
            from ui_stream.exceptions import InvalidEvent
            try:
                runtime.finish(status="CANCELLED", completion="ignored")
            except InvalidEvent:
                pass
            else:
                raise AssertionError("unsupported terminal status accepted")
            assert archive.result is None and not live.events and not runtime.closed
        """
        )

    def test_sse_encoder_rejects_invalid_control_cursor(self):
        self.run_contract(
            """
            from ui_stream.exceptions import InvalidCursor
            try:
                encode_sse_event(Event(data={"progress": 50}, stream_id="1-0\\nevent: injected"))
            except InvalidCursor:
                pass
            else:
                raise AssertionError("invalid SSE id accepted")
        """
        )

    def test_commit_integrity_is_visible_in_closed_summary(self):
        self.run_contract(
            """
            class TruncatedArchive(Archive):
                def commit(self, **kwargs):
                    super().commit(**kwargs)
                    return Integrity.TRUNCATED
            archive = TruncatedArchive()
            summaries = []
            runtime = StreamRuntime(binding=binding, publisher=live, codec=JsonEventCodec(),
                                    archive_store=archive, after_close=summaries.append)
            runtime.finish(status="SUCCESS", completion="result")
            assert runtime.archive_status == Integrity.TRUNCATED
            assert len(summaries) == 1 and summaries[0].truncated
            assert summaries[0].archive_status == Integrity.TRUNCATED
            """
        )

    def test_custom_consumer_uses_domain_events_and_snapshot_without_json_dependencies(self):
        self.run_contract(
            """
            import sys
            from examples.counter import make_counter
            from ui_stream import StreamPolicy
            for threshold in (1, 20):
                runtime, live, store = make_counter(policy=StreamPolicy(checkpoint_event_count=threshold))
                event = runtime.send(4)
                assert event.kind == "tick" and event.value == 4 and event.receipt == 1
                assert store.snapshot.latest == (4 if threshold == 1 else None)
                runtime.send(9)
                runtime.finish(status="DONE", completion=13)
                assert store.snapshot.latest == 9
                assert store.snapshot.terminal.value == "DONE" and store.snapshot.result == 13
                assert live.frames == [b"tick:4", b"tick:9", b"done:DONE"]
                assert live.results_at_terminal == [13]
                assert runtime.closed and runtime.pending_count == 0
            forbidden = ("pydantic", "redis", "django", "services", "rest_framework", "celery")
            assert not any(name.split(".")[0] in forbidden for name in sys.modules)
            """,
            bootstrap="",
        )

    def test_custom_live_failure_preserves_domain_snapshot_and_result(self):
        self.run_contract(
            """
            from examples.counter import CounterCodec, MemoryPublisher, MemorySnapshotStore, counter_binding
            from ui_stream import StreamRuntime, Integrity
            from ui_stream.exceptions import LiveUnavailable
            class OfflinePublisher(MemoryPublisher):
                def publish(self, event):
                    raise LiveUnavailable()
            binding = counter_binding()
            codec = CounterCodec()
            store = MemorySnapshotStore(binding)
            live = OfflinePublisher(codec, store)
            runtime = StreamRuntime(binding=binding, publisher=live, codec=codec, archive_store=store)
            runtime.send(4)
            runtime.finish(status="DONE", completion=4)
            assert store.snapshot.latest == 4 and store.snapshot.result == 4
            assert runtime.archive_status == Integrity.DEGRADED and runtime.closed
            """,
            bootstrap="",
        )

    def test_custom_snapshot_failure_recovers_and_commit_failure_keeps_open(self):
        self.run_contract(
            """
            from examples.counter import CounterCodec, MemoryPublisher, MemorySnapshotStore, counter_binding
            from ui_stream import StreamRuntime, StreamPolicy, Integrity
            from ui_stream.exceptions import ArchiveUnavailable
            class UnavailableStore(MemorySnapshotStore):
                fail_checkpoint = True
                fail_commit = True
                def checkpoint(self, **kwargs):
                    if self.fail_checkpoint:
                        raise ArchiveUnavailable()
                    return super().checkpoint(**kwargs)
                def commit(self, **kwargs):
                    if self.fail_commit:
                        raise ArchiveUnavailable()
                    return super().commit(**kwargs)
            for threshold in (1, 20):
                binding = counter_binding()
                codec = CounterCodec()
                store = UnavailableStore(binding)
                live = MemoryPublisher(codec, store)
                runtime = StreamRuntime(binding=binding, publisher=live, codec=codec, archive_store=store,
                                        policy=StreamPolicy(checkpoint_event_count=threshold, max_archive_bytes=45))
                for value in range(20):
                    runtime.send(value)
                assert runtime.pending_count <= 5
                assert runtime.archive_status == Integrity.TRUNCATED
                assert store.snapshot.latest is None and not runtime.closed
                try:
                    runtime.finish(status="DONE", completion=190)
                except ArchiveUnavailable:
                    pass
                else:
                    raise AssertionError("commit error swallowed")
                assert runtime.pending_count > 0 and not runtime.closed
                assert store.snapshot.result is None and len(live.frames) == 20
                store.fail_checkpoint = False
                store.fail_commit = False
                runtime.finish(status="DONE", completion=190)
                assert store.snapshot.result == 190 and store.snapshot.terminal.value == "DONE"
                assert runtime.closed and runtime.pending_count == 0
                assert live.results_at_terminal == [190]
            """,
            bootstrap="",
        )

    def test_after_close_is_frozen_once_and_failure_cannot_undo_commit(self):
        self.run_contract(
            """
            from dataclasses import FrozenInstanceError
            from examples.counter import make_counter
            summaries = []
            def observer(summary):
                summaries.append(summary)
                try:
                    summary.event_count = 100
                except FrozenInstanceError:
                    pass
                else:
                    raise AssertionError("mutable notification")
                raise RuntimeError("telemetry unavailable")
            runtime, live, store = make_counter(after_close=observer)
            runtime.send(4)
            runtime.finish(status="DONE", completion=4)
            runtime.close_for_retry()
            assert runtime.closed and store.snapshot.result == 4
            assert len(summaries) == 1 and summaries[0].event_count == 1
            assert not hasattr(summaries[0], "runtime")
            assert live.results_at_terminal == [4]
            """,
            bootstrap="",
        )

    def test_custom_checkpoint_recovers_all_pending_values_without_closing(self):
        self.run_contract(
            """
            from examples.counter import CounterCodec, MemoryPublisher, MemorySnapshotStore, counter_binding
            from ui_stream import StreamRuntime, StreamPolicy, Integrity
            from ui_stream.exceptions import ArchiveUnavailable
            class RecoveringStore(MemorySnapshotStore):
                unavailable = True
                def checkpoint(self, **kwargs):
                    if self.unavailable:
                        raise ArchiveUnavailable()
                    return super().checkpoint(**kwargs)
            binding = counter_binding()
            codec = CounterCodec()
            store = RecoveringStore(binding)
            publisher = MemoryPublisher(codec, store)
            runtime = StreamRuntime(binding=binding, publisher=publisher, codec=codec, archive_store=store,
                                    policy=StreamPolicy(checkpoint_event_count=1))
            runtime.send(4)
            assert runtime.pending_count == 1 and store.snapshot.latest is None and not runtime.closed
            store.unavailable = False
            runtime.send(9)
            assert runtime.pending_count == 0 and store.snapshot.latest == 9 and not runtime.closed
            assert runtime.archive_status == Integrity.DEGRADED
            runtime.finish(status="DONE", completion=13)
            assert store.snapshot.result == 13 and publisher.results_at_terminal == [13]
            """,
            bootstrap="",
        )

    def test_public_assembly_types_support_a_standard_library_only_host(self):
        self.run_contract(
            """
            from uuid import uuid4
            from ui_stream import (
                ArchiveStore, Binding, Checkpoint, EncodingTarget, EventCodec,
                ExecutionBinding, Integrity, LivePublisher, StreamPolicy, StreamRuntime,
            )
            from examples.counter import CounterCodec, MemoryPublisher, MemorySnapshotStore
            binding: ExecutionBinding = Binding("synthetic", "task", uuid4())
            codec: EventCodec = CounterCodec()
            archive: ArchiveStore = MemorySnapshotStore(binding)
            publisher: LivePublisher = MemoryPublisher(codec, archive)
            runtime = StreamRuntime(binding=binding, codec=codec, archive_store=archive,
                                    publisher=publisher, policy=StreamPolicy(checkpoint_event_count=1))
            runtime.send(4)
            assert archive.snapshot.latest == 4
            assert codec.encode(codec.business(4), EncodingTarget.LIVE) == b"tick:4"
            assert Checkpoint(Integrity.COMPLETE).capacity_exhausted is False
            runtime.close_for_retry()
            assert runtime.closed
            assert not any(name.split(".")[0] in ("redis", "pydantic", "django") for name in sys.modules)
            """,
            bootstrap="",
        )

    def test_close_for_retry_closes_after_archive_failure_without_claiming_persistence(self):
        self.run_contract(
            """
            runtime.send({"progress": 50})
            archive.fail_checkpoint = True
            runtime.close_for_retry()
            assert runtime.closed and runtime.pending_count == 1
            assert runtime.archive_status == Integrity.DEGRADED
            assert archive.events == [] and archive.result is None
            archive.fail_checkpoint = False
            runtime.close_for_retry()
            assert archive.events == [] and len(live.events) == 1
            """
        )

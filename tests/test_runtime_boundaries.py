"""Capacity and Redis parser boundaries must preserve truthful integrity and cursors."""

import unittest
from types import SimpleNamespace
from uuid import uuid4

from examples.counter import CounterCodec, MemoryPublisher, MemorySnapshotStore, counter_binding, make_counter
from ui_stream import Integrity, StreamPolicy, StreamRuntime
from ui_stream.codec import JsonEventCodec
from ui_stream.exceptions import ClosedStream, InvalidCursor, InvalidEvent, LiveUnavailable
from ui_stream.reader import iter_live, normalize_cursor
from ui_stream.redis import RedisLiveStore
from ui_stream.types import Binding, Checkpoint


class Live:
    def publish(self, event):
        return event


class ExactCapacityStore:
    """The first accepted event fills storage without dropping any event."""

    def __init__(self):
        self.events = []
        self.committed_status = None

    def checkpoint(self, *, binding, events, archive_status):
        self.events.extend(events)
        return Checkpoint(archive_status=archive_status, capacity_exhausted=True)

    def touch_activity(self, *, binding):
        pass

    def commit(self, *, binding, events, terminal_event, archive_status, completion):
        self.events.extend(events)
        self.committed_status = archive_status
        return archive_status


class RedisClient:
    def __init__(self, *, decode_failure=False, entries=()):
        self.decode_failure = decode_failure
        self.entries = entries
        self.cursors = []

    def register_script(self, source):
        return lambda **kwargs: b"1-0"

    def xread(self, keys, **kwargs):
        self.cursors.append(next(iter(keys.values())))
        if self.decode_failure:
            # redis-py's parser can fail before XREAD returns any entry IDs.
            b"\xff".decode("utf-8")
        return self.entries


class RuntimeReviewRegressions(unittest.TestCase):
    def make_runtime(self):
        store = ExactCapacityStore()
        summaries = []
        runtime = StreamRuntime(
            binding=Binding("job:synthetic", "task", uuid4()),
            publisher=Live(),
            codec=JsonEventCodec(),
            archive_store=store,
            policy=StreamPolicy(checkpoint_event_count=1),
            after_close=summaries.append,
        )
        return runtime, store, summaries

    def test_exactly_full_without_dropped_event_remains_complete(self):
        runtime, store, summaries = self.make_runtime()
        runtime.send(1)
        runtime.finish(status="SUCCESS", completion=1)
        self.assertEqual(runtime.archive_status, Integrity.COMPLETE)
        self.assertEqual(store.committed_status, Integrity.COMPLETE)
        self.assertFalse(summaries[0].truncated)

    def test_capacity_stop_marks_truncated_only_when_next_event_is_dropped(self):
        runtime, store, summaries = self.make_runtime()
        runtime.send(1)
        self.assertEqual(runtime.archive_status, Integrity.COMPLETE)
        runtime.send(2)
        self.assertEqual(runtime.archive_status, Integrity.TRUNCATED)
        runtime.finish(status="SUCCESS", completion=2)
        self.assertEqual([event.data for event in store.events], [1])
        self.assertEqual(store.committed_status, Integrity.TRUNCATED)
        self.assertTrue(summaries[0].truncated)

    def test_initial_integrity_is_reflected_in_retry_summary(self):
        for status in (Integrity.DEGRADED, Integrity.TRUNCATED):
            with self.subTest(status=status):
                summaries = []
                runtime = StreamRuntime(
                    binding=Binding("job:synthetic", "task", uuid4()),
                    publisher=Live(),
                    codec=JsonEventCodec(),
                    archive_store=ExactCapacityStore(),
                    archive_status=status,
                    after_close=summaries.append,
                )
                runtime.finish_retry()
                self.assertEqual(summaries[0].archive_status, status)
                self.assertEqual(summaries[0].degraded, status == Integrity.DEGRADED)
                self.assertEqual(summaries[0].truncated, status == Integrity.TRUNCATED)

    def test_cursor_accepts_only_ascii_uint64_components(self):
        maximum = str(2**64 - 1)
        for value in ("0-0", "1-0", maximum + "-" + maximum):
            self.assertEqual(normalize_cursor(value), value)
        for value in ("١-٠", str(2**64) + "-0", "0-" + str(2**64), "1-" + "9" * 100):
            with self.subTest(value=value), self.assertRaises(InvalidCursor):
                normalize_cursor(value)

    def test_decoded_string_responses_are_supported_on_normal_path(self):
        client = RedisClient(entries=[("key", [("1-0", {"payload": '{"data":1}'})])])
        batch = RedisLiveStore(client=client).read(redis_key="key", after_id="0-0", block_ms=0)
        self.assertEqual([event.data for event in batch.events], [1])
        self.assertEqual(batch.last_seen_stream_id, "1-0")

    def test_predecoded_payload_failure_is_live_unavailable(self):
        client = RedisClient(decode_failure=True)
        with self.assertRaises(LiveUnavailable):
            RedisLiveStore(client=client).read(redis_key="key", after_id="0-0", block_ms=0)
        self.assertEqual(client.cursors, ["0-0"])

    def test_reader_ends_on_parser_failure_without_guessing_a_cursor(self):
        client = RedisClient(decode_failure=True)
        events = list(
            iter_live(
                redis_store=RedisLiveStore(client=client),
                redis_key="key",
                cursor="0-0",
                block_ms=0,
            )
        )
        self.assertEqual(events, [])
        self.assertEqual(client.cursors, ["0-0"])

    def test_raw_dirty_payload_is_skipped_and_real_cursor_advances(self):
        client = RedisClient(
            entries=[
                (
                    b"key",
                    [
                        (b"1-0", {b"payload": b"\xff"}),
                        (b"1-1", {b"payload": b'{"data":NaN}'}),
                        (b"1-2", {b"payload": b'{"data":1}'}),
                    ],
                )
            ]
        )
        batch = RedisLiveStore(client=client).read(redis_key="key", after_id="0-0", block_ms=0)
        self.assertEqual([event.data for event in batch.events], [1])
        self.assertEqual(batch.last_seen_stream_id, "1-2")


class RuntimePolicyTest(unittest.TestCase):
    def test_default_policy_is_fixed_and_keeps_pending_until_finish(self):
        runtime, publisher, store = make_counter()
        policy = runtime.policy
        self.assertIsInstance(policy, StreamPolicy)
        runtime.send(4)
        self.assertIs(runtime.policy, policy)
        self.assertEqual(publisher.frames, [b"tick:4"])
        self.assertEqual(runtime.pending_count, 1)
        self.assertIsNone(store.snapshot.latest)
        runtime.finish(status="DONE", completion=4)
        self.assertEqual(store.snapshot.latest, 4)
        self.assertEqual(store.snapshot.result, 4)
        self.assertEqual(publisher.results_at_terminal, [4])

    def test_explicit_fixed_policy_keeps_identity_and_checkpoint_behavior(self):
        policy = StreamPolicy(checkpoint_event_count=1)
        runtime, publisher, store = make_counter(policy=policy)
        self.assertIs(runtime.policy, policy)
        event = runtime.send(4)
        self.assertEqual(event.receipt, 1)
        self.assertEqual(publisher.frames, [b"tick:4"])
        self.assertEqual(runtime.pending_count, 0)
        self.assertEqual(store.snapshot.latest, 4)
        self.assertIs(runtime.policy, policy)

    def test_callable_stream_policy_remains_a_fixed_value(self):
        class CallablePolicy(StreamPolicy):
            def __call__(self):
                raise AssertionError("fixed policy must not be invoked")

        policy = CallablePolicy(checkpoint_event_count=1)
        runtime, publisher, store = make_counter(policy=policy)
        self.assertIs(runtime.policy, policy)
        runtime.send(4)
        self.assertIs(runtime.policy, policy)
        self.assertEqual(publisher.frames, [b"tick:4"])
        self.assertEqual(store.snapshot.latest, 4)

    def test_legacy_non_callable_policy_keeps_existing_behavior(self):
        policy = SimpleNamespace(
            max_event_bytes=1024,
            max_events=10,
            max_live_bytes=1024,
            max_archive_bytes=1024,
            activity_interval_seconds=3600,
            checkpoint_event_count=1,
            checkpoint_bytes=1024,
        )
        runtime, publisher, store = make_counter(policy=policy)
        self.assertIs(runtime.policy, policy)
        runtime.send(4)
        self.assertEqual(publisher.frames, [b"tick:4"])
        self.assertEqual(store.snapshot.latest, 4)
        self.assertEqual(runtime.pending_count, 0)

    def test_policy_property_remains_read_only(self):
        runtime, publisher, store = make_counter(policy=StreamPolicy(checkpoint_event_count=1))
        with self.assertRaises(AttributeError):
            runtime.policy = StreamPolicy(max_event_bytes=0)
        runtime.send(4)
        self.assertEqual(publisher.frames, [b"tick:4"])
        self.assertEqual(store.snapshot.latest, 4)

    def test_factory_is_lazy_and_policy_reads_current_value(self):
        current = [StreamPolicy(checkpoint_event_count=2)]
        calls = []

        def policy_factory():
            calls.append(None)
            return current[0]

        runtime, publisher, store = make_counter(policy=policy_factory)
        self.assertEqual(calls, [])
        self.assertIs(runtime.policy, current[0])
        current[0] = StreamPolicy(checkpoint_event_count=1)
        self.assertIs(runtime.policy, current[0])
        self.assertEqual(len(calls), 2)
        self.assertEqual(publisher.frames, [])
        self.assertIsNone(store.snapshot.latest)

    def test_factory_result_must_be_stream_policy(self):
        runtime, publisher, store = make_counter(policy=lambda: None)
        with self.assertRaisesRegex(TypeError, "policy factory must return StreamPolicy"):
            _ = runtime.policy
        self.assertEqual(publisher.frames, [])
        self.assertIsNone(store.snapshot.latest)

    def test_factory_exception_propagates_on_policy_read(self):
        failure = RuntimeError("synthetic configuration failure")

        def policy_factory():
            raise failure

        runtime, publisher, store = make_counter(policy=policy_factory)
        with self.assertRaises(RuntimeError) as raised:
            _ = runtime.policy
        self.assertIs(raised.exception, failure)
        self.assertEqual(publisher.frames, [])
        self.assertIsNone(store.snapshot.latest)

    def test_send_uses_one_snapshot_when_publisher_changes_settings(self):
        current = [StreamPolicy(checkpoint_event_count=2)]
        updated = StreamPolicy(checkpoint_event_count=1)
        calls = []

        def policy_factory():
            calls.append(None)
            return current[0]

        class UpdatingPublisher(MemoryPublisher):
            def publish(self, event):
                published = super().publish(event)
                current[0] = updated
                return published

        binding = counter_binding()
        codec = CounterCodec()
        store = MemorySnapshotStore(binding)
        publisher = UpdatingPublisher(codec, store)
        runtime = StreamRuntime(
            binding=binding, codec=codec, publisher=publisher, archive_store=store, policy=policy_factory
        )
        runtime.send(4)
        self.assertEqual(runtime.pending_count, 1)
        self.assertIsNone(store.snapshot.latest)
        self.assertEqual(len(calls), 1)
        runtime.send(9)
        self.assertEqual(runtime.pending_count, 0)
        self.assertEqual(store.snapshot.latest, 9)
        self.assertEqual(publisher.frames, [b"tick:4", b"tick:9"])
        self.assertEqual(len(calls), 2)

    def test_send_calls_factory_once_across_archive_and_activity_paths(self):
        for policy in (
            StreamPolicy(checkpoint_event_count=1),
            StreamPolicy(max_events=0, activity_interval_seconds=0),
            StreamPolicy(max_archive_bytes=0, activity_interval_seconds=0),
        ):
            with self.subTest(policy=policy):
                calls = []

                def policy_factory():
                    calls.append(None)
                    return policy

                runtime, publisher, _ = make_counter(policy=policy_factory)
                runtime.send(4)
                self.assertEqual(publisher.frames, [b"tick:4"])
                self.assertEqual(runtime.pending_count, 0)
                self.assertEqual(len(calls), 1)

    def test_dynamic_activity_interval_refreshes_binding_after_archive_stops(self):
        current = [StreamPolicy(max_events=0, activity_interval_seconds=3600)]
        calls = []
        touched = []

        def policy_factory():
            calls.append(None)
            return current[0]

        class RecordingActivityStore(MemorySnapshotStore):
            def touch_activity(self, *, binding):
                super().touch_activity(binding=binding)
                touched.append(binding)

        binding = counter_binding()
        codec = CounterCodec()
        store = RecordingActivityStore(binding)
        publisher = MemoryPublisher(codec, store)
        runtime = StreamRuntime(
            binding=binding, codec=codec, publisher=publisher, archive_store=store, policy=policy_factory
        )
        runtime.send(4)
        self.assertEqual(touched, [])
        self.assertEqual(len(calls), 1)
        current[0] = StreamPolicy(max_events=0, activity_interval_seconds=0)
        runtime.send(9)
        self.assertEqual(touched, [binding])
        self.assertEqual(len(calls), 2)
        self.assertEqual(publisher.frames, [b"tick:4", b"tick:9"])
        self.assertEqual(runtime.pending_count, 0)
        self.assertIsNone(store.snapshot.latest)
        self.assertEqual(runtime.archive_status, Integrity.TRUNCATED)

    def test_oversized_send_still_evaluates_factory_once(self):
        calls = []

        def policy_factory():
            calls.append(None)
            return StreamPolicy(max_event_bytes=1)

        runtime, publisher, store = make_counter(policy=policy_factory)
        self.assertIsNone(runtime.send(4))
        self.assertEqual(len(calls), 1)
        self.assertEqual(publisher.frames, [])
        self.assertIsNone(store.snapshot.latest)
        self.assertEqual(runtime.pending_count, 0)
        self.assertEqual(runtime.archive_status, Integrity.TRUNCATED)

    def test_factory_failure_before_send_has_no_writes_or_counter_changes(self):
        failure = LiveUnavailable("synthetic configuration failure")
        current = [failure]
        calls = []
        summaries = []

        def policy_factory():
            calls.append(None)
            if isinstance(current[0], Exception):
                raise current[0]
            return current[0]

        runtime, publisher, store = make_counter(policy=policy_factory, after_close=summaries.append)
        with self.assertRaises(LiveUnavailable) as raised:
            runtime.send(4)
        self.assertIs(raised.exception, failure)
        self.assertEqual(publisher.frames, [])
        self.assertEqual(runtime.pending_count, 0)
        self.assertIsNone(store.snapshot.latest)
        self.assertFalse(runtime.closed)
        self.assertEqual(runtime.archive_status, Integrity.COMPLETE)
        current[0] = StreamPolicy(checkpoint_event_count=1)
        runtime.send(9)
        runtime.finish(status="DONE", completion=9)
        self.assertEqual(len(calls), 2)
        self.assertEqual(store.snapshot.latest, 9)
        self.assertEqual(store.snapshot.result, 9)
        self.assertEqual(summaries[0].event_count, 1)
        self.assertEqual(summaries[0].event_bytes, 6)
        self.assertFalse(summaries[0].degraded)

    def test_invalid_factory_result_before_send_has_no_writes_or_counter_changes(self):
        calls = []
        summaries = []

        def policy_factory():
            calls.append(None)
            return None

        runtime, publisher, store = make_counter(policy=policy_factory, after_close=summaries.append)
        with self.assertRaisesRegex(TypeError, "policy factory must return StreamPolicy"):
            runtime.send(4)
        self.assertEqual(publisher.frames, [])
        self.assertEqual(runtime.pending_count, 0)
        self.assertIsNone(store.snapshot.latest)
        self.assertEqual(runtime.archive_status, Integrity.COMPLETE)
        runtime.finish(status="DONE", completion=0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(summaries[0].event_count, 0)
        self.assertEqual(summaries[0].event_bytes, 0)
        self.assertEqual(store.snapshot.result, 0)

    def test_closed_send_and_finish_paths_do_not_evaluate_factory(self):
        for retry in (False, True):
            with self.subTest(retry=retry):
                calls = []

                def policy_factory():
                    calls.append(None)
                    return StreamPolicy()

                runtime, _, _ = make_counter(policy=policy_factory)
                if retry:
                    runtime.finish_retry()
                else:
                    runtime.finish(status="DONE", completion=0)
                self.assertTrue(runtime.closed)
                with self.assertRaises(ClosedStream):
                    runtime.send(4)
                self.assertEqual(calls, [])

    def test_dynamic_limits_do_not_reset_cumulative_budget(self):
        current = [StreamPolicy(max_events=1, checkpoint_event_count=1)]
        calls = []
        summaries = []

        def policy_factory():
            calls.append(None)
            return current[0]

        runtime, publisher, store = make_counter(policy=policy_factory, after_close=summaries.append)
        runtime.send(1)
        current[0] = StreamPolicy(max_events=2, checkpoint_event_count=1)
        runtime.send(2)
        runtime.send(3)
        self.assertEqual(store.snapshot.latest, 2)
        self.assertEqual(publisher.frames, [b"tick:1", b"tick:2", b"tick:3"])
        self.assertEqual(runtime.archive_status, Integrity.TRUNCATED)
        runtime.finish(status="DONE", completion=6)
        self.assertEqual(store.snapshot.result, 6)
        self.assertEqual(summaries[0].event_count, 3)
        self.assertEqual(len(calls), 3)

    def test_increased_live_budget_does_not_restart_stopped_channel(self):
        current = [StreamPolicy(max_live_bytes=0, checkpoint_event_count=1)]
        summaries = []
        runtime, publisher, store = make_counter(policy=lambda: current[0], after_close=summaries.append)
        runtime.send(1)
        current[0] = StreamPolicy(max_live_bytes=1024, checkpoint_event_count=1)
        runtime.send(2)
        self.assertEqual(publisher.frames, [])
        self.assertEqual(store.snapshot.latest, 2)
        self.assertEqual(runtime.archive_status, Integrity.DEGRADED)
        runtime.finish(status="DONE", completion=3)
        self.assertEqual(publisher.frames, [b"done:DONE"])
        self.assertEqual(summaries[0].event_count, 2)
        self.assertEqual(store.snapshot.result, 3)

    def test_invalid_business_input_still_evaluates_factory_once(self):
        calls = []
        summaries = []

        def policy_factory():
            calls.append(None)
            return StreamPolicy()

        runtime, publisher, store = make_counter(policy=policy_factory, after_close=summaries.append)
        with self.assertRaises(InvalidEvent):
            runtime.send(-1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(publisher.frames, [])
        self.assertIsNone(store.snapshot.latest)
        self.assertEqual(runtime.pending_count, 0)
        runtime.finish(status="DONE", completion=0)
        self.assertEqual(summaries[0].event_count, 0)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

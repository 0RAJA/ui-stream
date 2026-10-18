"""Capacity and Redis parser boundaries must preserve truthful integrity and cursors."""

import unittest
from uuid import uuid4

from ui_stream import Integrity, StreamPolicy, StreamRuntime
from ui_stream.codec import JsonEventCodec
from ui_stream.exceptions import InvalidCursor, LiveUnavailable
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

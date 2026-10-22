"""Invalid policy input must fail before a send can perform external I/O."""

import unittest
from enum import IntEnum

from examples.counter import CounterCodec, MemoryPublisher, MemorySnapshotStore, counter_binding, make_counter
from ui_stream import StreamPolicy, StreamRuntime

FIELDS = (
    "max_event_bytes",
    "max_events",
    "max_live_bytes",
    "max_archive_bytes",
    "checkpoint_event_count",
    "checkpoint_bytes",
    "activity_interval_seconds",
)


class StreamPolicyTest(unittest.TestCase):
    def test_negative_limits_are_rejected_at_construction(self):
        for field in FIELDS:
            with self.subTest(field=field), self.assertRaises(ValueError):
                StreamPolicy(**{field: -1})

    def test_non_integer_and_boolean_limits_are_rejected(self):
        for field in FIELDS:
            for value in (True, False, None, "1", 1.5, float("nan"), float("inf")):
                with self.subTest(field=field, value=value), self.assertRaises(TypeError):
                    StreamPolicy(**{field: value})

    def test_integer_subclasses_are_rejected(self):
        class Limit(IntEnum):
            ONE = 1

        class CustomInt(int):
            pass

        for field in FIELDS:
            for value in (Limit.ONE, CustomInt(1)):
                with self.subTest(field=field, value=value), self.assertRaises(TypeError):
                    StreamPolicy(**{field: value})

    def test_zero_budget_and_checkpoint_semantics_remain_distinct(self):
        cases = (
            ("max_event_bytes", True, 0, 0, None),
            ("max_events", False, 1, 0, None),
            ("max_live_bytes", False, 0, 1, None),
            ("max_archive_bytes", False, 1, 0, None),
            ("checkpoint_event_count", False, 1, 0, 1),
            ("checkpoint_bytes", False, 1, 0, 1),
            ("activity_interval_seconds", False, 1, 0, 1),
        )
        for field, rejected, live_count, pending, latest in cases:
            with self.subTest(field=field):
                runtime, publisher, store = make_counter(policy=StreamPolicy(**{field: 0}))
                event = runtime.send(1)
                self.assertEqual(event is None, rejected)
                self.assertEqual(len(publisher.frames), live_count)
                self.assertEqual(runtime.pending_count, pending)
                self.assertEqual(store.snapshot.latest, latest)

    def test_zero_byte_budgets_still_accept_a_zero_byte_custom_encoding(self):
        class EmptyEncoding(CounterCodec):
            def encode(self, event, target):
                return b""

        binding = counter_binding()
        codec = EmptyEncoding()
        store = MemorySnapshotStore(binding)
        publisher = MemoryPublisher(codec, store)
        runtime = StreamRuntime(
            binding=binding,
            codec=codec,
            publisher=publisher,
            archive_store=store,
            policy=StreamPolicy(max_event_bytes=0, max_live_bytes=0, max_archive_bytes=0, checkpoint_event_count=1),
        )
        self.assertIsNotNone(runtime.send(1))
        self.assertEqual(store.snapshot.latest, 1)
        self.assertEqual(publisher.frames, [b""])

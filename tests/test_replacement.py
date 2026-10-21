"""Execution replacement notifies only the previous live stream after host fencing."""

import unittest

from demos.sqlite.constants import EventKind
from demos.sqlite.events import DemoCodec
from examples.counter import CounterCodec, MemoryPublisher, MemorySnapshotStore, counter_binding, make_counter
from ui_stream import Integrity, StreamPolicy, StreamRuntime
from ui_stream.codec import ControlEvent, Event, JsonEventCodec
from ui_stream.exceptions import ClosedStream, InvalidEvent, LiveUnavailable
from ui_stream.types import EncodingTarget


class CodecResetTest(unittest.TestCase):
    def reset(self, codec, reason):
        reset = getattr(codec, "reset", None)
        self.assertTrue(callable(reset), "EventCodec must construct reset events")
        return reset(reason)

    def test_json_reset_uses_factory_without_a_synthetic_cursor(self):
        class TaggedEvent(Event):
            tag: str = "synthetic"

        codec = JsonEventCodec(event_factory=TaggedEvent)
        event = self.reset(codec, "execution_replaced")
        self.assertIsInstance(event, TaggedEvent)
        self.assertEqual(event.event, ControlEvent.STREAM_RESET)
        self.assertEqual(event.data, {"reason": "execution_replaced"})
        self.assertIsNone(event.stream_id)
        self.assertEqual(
            codec.encode(event, EncodingTarget.LIVE),
            b'{"event":"platform.stream_reset","data":{"reason":"execution_replaced"}}',
        )

    def test_reset_reason_must_be_text(self):
        for codec in (JsonEventCodec(), CounterCodec(), DemoCodec()):
            for reason in (None, 3, {"reason": "nested"}):
                with self.subTest(codec=type(codec).__name__, reason=reason):
                    reset = getattr(codec, "reset", None)
                    self.assertTrue(callable(reset), "EventCodec must construct reset events")
                    with self.assertRaises(InvalidEvent):
                        reset(reason)

    def test_custom_codecs_keep_their_own_reset_representation(self):
        counter = self.reset(CounterCodec(), "execution_replaced")
        self.assertEqual(CounterCodec().encode(counter, EncodingTarget.LIVE), b"reset:execution_replaced")
        self.assertIsNone(counter.receipt)
        demo = self.reset(DemoCodec(), "execution_replaced")
        self.assertEqual(demo.kind, EventKind.RESET)
        self.assertEqual(demo.message, "execution_replaced")
        self.assertIsNone(demo.cursor)


class ReplacementTest(unittest.TestCase):
    def announce(self, runtime, previous):
        announce = getattr(runtime, "announce_replacement", None)
        self.assertTrue(callable(announce), "Runtime must support previous-stream notification")
        return announce(previous)

    def with_codec(self, codec, **options):
        binding = counter_binding()
        store = MemorySnapshotStore(binding)
        publisher = MemoryPublisher(codec, store)
        runtime = StreamRuntime(binding=binding, codec=codec, archive_store=store, publisher=publisher, **options)
        return runtime, publisher, store

    def test_only_previous_publisher_receives_reset_without_archiving_it(self):
        summaries = []
        calls = []

        def policy():
            calls.append(None)
            return StreamPolicy()

        runtime, publisher, store = make_counter(policy=policy, after_close=summaries.append)
        _, previous, old_store = make_counter()
        runtime.send(4)
        self.assertTrue(self.announce(runtime, previous))
        self.assertEqual(previous.frames, [b"reset:execution_replaced"])
        self.assertEqual(publisher.frames, [b"tick:4"])
        self.assertEqual(runtime.pending_count, 1)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(store.snapshot.latest)
        self.assertIsNone(old_store.snapshot.latest)
        self.assertFalse(runtime.closed)
        runtime.finish(status="DONE", completion=4)
        self.assertEqual(store.snapshot.latest, 4)
        self.assertEqual(store.snapshot.result, 4)
        self.assertEqual(summaries[0].event_count, 1)
        self.assertEqual(summaries[0].event_bytes, 6)
        self.assertFalse(summaries[0].degraded)

    def test_uncertain_transport_failure_degrades_new_runtime_without_retry(self):
        for failure in (
            LiveUnavailable("synthetic hidden transport detail"),
            OSError("synthetic hidden transport detail"),
        ):
            with self.subTest(failure=type(failure).__name__):

                class UncertainPublisher(MemoryPublisher):
                    def publish(self, event):
                        super().publish(event)
                        raise failure

                summaries = []
                runtime, publisher, store = make_counter(after_close=summaries.append)
                _, old, old_store = make_counter()
                previous = UncertainPublisher(CounterCodec(), old_store)
                with self.assertLogs("ui_stream.runtime", level="WARNING") as captured:
                    self.assertFalse(self.announce(runtime, previous))
                self.assertEqual(previous.frames, [b"reset:execution_replaced"])
                self.assertEqual(publisher.frames, [])
                self.assertEqual(runtime.archive_status, Integrity.DEGRADED)
                self.assertIsNone(store.snapshot.latest)
                self.assertIsNone(old_store.snapshot.latest)
                self.assertNotIn("hidden transport detail", "\n".join(captured.output))
                self.assertEqual(captured.records[0].execution_id, str(runtime.execution_id))
                self.assertEqual(captured.records[0].error_type, type(failure).__name__)
                runtime.finish(status="DONE", completion=0)
                self.assertTrue(summaries[0].degraded)
                self.assertEqual(summaries[0].event_count, 0)

    def test_unconfigured_port_errors_and_interrupts_propagate(self):
        for failure in (ValueError("synthetic publisher contract"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):

                class BrokenPublisher:
                    def publish(self, event):
                        raise failure

                runtime, publisher, store = make_counter()
                with self.assertRaises(type(failure)) as raised:
                    self.announce(runtime, BrokenPublisher())
                self.assertIs(raised.exception, failure)
                self.assertEqual(runtime.archive_status, Integrity.COMPLETE)
                self.assertEqual(publisher.frames, [])
                self.assertIsNone(store.snapshot.latest)

    def test_reset_construction_errors_are_not_treated_as_transport_failures(self):
        failure = LiveUnavailable("synthetic codec failure")

        class BrokenCodec(CounterCodec):
            def reset(self, reason):
                raise failure

        runtime, publisher, store = self.with_codec(BrokenCodec())
        _, previous, _ = make_counter()
        with self.assertRaises(LiveUnavailable) as raised:
            self.announce(runtime, previous)
        self.assertIs(raised.exception, failure)
        self.assertEqual(previous.frames, [])
        self.assertEqual(runtime.archive_status, Integrity.COMPLETE)
        self.assertIsNone(store.snapshot.latest)

    def test_reset_encoding_errors_are_not_treated_as_transport_failures(self):
        failure = LiveUnavailable("synthetic encoding failure")

        class BrokenCodec(CounterCodec):
            def encode(self, event, target):
                if event.kind == "reset":
                    raise failure
                return super().encode(event, target)

        runtime, _, store = self.with_codec(BrokenCodec())
        _, previous, _ = make_counter()
        with self.assertRaises(LiveUnavailable) as raised:
            self.announce(runtime, previous)
        self.assertIs(raised.exception, failure)
        self.assertEqual(previous.frames, [])
        self.assertEqual(runtime.archive_status, Integrity.COMPLETE)
        self.assertIsNone(store.snapshot.latest)

    def test_notification_uses_configured_transport_errors_without_restarting_limits(self):
        class TransportFailure(RuntimeError):
            pass

        class OfflinePublisher:
            def publish(self, event):
                raise TransportFailure()

        runtime, publisher, _ = self.with_codec(
            CounterCodec(), policy=StreamPolicy(max_live_bytes=0), live_errors=(TransportFailure,)
        )
        runtime.send(4)
        self.assertFalse(self.announce(runtime, OfflinePublisher()))
        self.assertEqual(publisher.frames, [])
        _, previous, _ = make_counter()
        self.assertTrue(self.announce(runtime, previous))
        self.assertEqual(previous.frames, [b"reset:execution_replaced"])
        self.assertEqual(runtime.archive_status, Integrity.DEGRADED)

    def test_notification_failure_preserves_truncated_integrity(self):
        class OfflinePublisher:
            def publish(self, event):
                raise LiveUnavailable()

        runtime, _, _ = self.with_codec(CounterCodec(), archive_status=Integrity.TRUNCATED)
        self.assertFalse(self.announce(runtime, OfflinePublisher()))
        self.assertEqual(runtime.archive_status, Integrity.TRUNCATED)

    def test_closed_runtime_rejects_notification_before_touching_previous_stream(self):
        runtime, _, _ = make_counter()
        _, previous, _ = make_counter()
        runtime.finish_retry()
        with self.assertRaises(ClosedStream):
            self.announce(runtime, previous)
        self.assertEqual(previous.frames, [])

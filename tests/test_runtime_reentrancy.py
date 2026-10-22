"""Port callbacks may inspect a Runtime but must not nest its write operations."""

import unittest

from examples.counter import CounterCodec, MemoryPublisher, MemorySnapshotStore, counter_binding
from ui_stream import EncodingTarget, Integrity, StreamPolicy, StreamRuntime, exceptions
from ui_stream.exceptions import LiveUnavailable


class CallbackPublisher(MemoryPublisher):
    def __init__(self, codec, store):
        super().__init__(codec, store)
        self.callback = None

    def publish(self, event):
        published = super().publish(event)
        if self.callback is not None and event.kind == "tick":
            callback, self.callback = self.callback, None
            callback()
        return published


class CallbackStore(MemorySnapshotStore):
    def __init__(self, binding):
        super().__init__(binding)
        self.checkpoint_callback = None
        self.commit_callback = None
        self.touch_callback = None

    def checkpoint(self, **kwargs):
        if self.checkpoint_callback is not None:
            callback, self.checkpoint_callback = self.checkpoint_callback, None
            callback()
        return super().checkpoint(**kwargs)

    def commit(self, **kwargs):
        if self.commit_callback is not None:
            callback, self.commit_callback = self.commit_callback, None
            callback()
        return super().commit(**kwargs)

    def touch_activity(self, **kwargs):
        if self.touch_callback is not None:
            callback, self.touch_callback = self.touch_callback, None
            callback()
        return super().touch_activity(**kwargs)


class RuntimeReentrancyTest(unittest.TestCase):
    def make_runtime(self, *, policy=None, codec=None, after_close=None):
        binding = counter_binding()
        codec = codec or CounterCodec()
        store = CallbackStore(binding)
        publisher = CallbackPublisher(codec, store)
        runtime = StreamRuntime(
            binding=binding,
            codec=codec,
            publisher=publisher,
            archive_store=store,
            policy=policy or StreamPolicy(checkpoint_event_count=1000, activity_interval_seconds=999999),
            live_errors=(Exception,),
            archive_errors=(Exception,),
            after_close=after_close,
        )
        return runtime, publisher, store

    def assert_reentrant(self, operation):
        with self.assertRaises(RuntimeError) as raised:
            operation()
        self.assertIs(type(raised.exception), exceptions.ReentrantStreamOperation)

    def test_rejected_publish_reentry_preserves_degradation_across_later_calls(self):
        runtime, publisher, store = self.make_runtime(policy=StreamPolicy(max_live_bytes=6))
        publisher.callback = lambda: runtime.send(2)

        self.assert_reentrant(lambda: runtime.send(1))

        self.assertEqual(publisher.frames, [b"tick:1"])
        self.assertEqual(runtime.pending_count, 0)
        self.assertEqual(runtime.archive_status, Integrity.DEGRADED)
        self.assertIsNone(store.snapshot.latest)
        publisher.callback = None
        runtime.send(3)
        runtime.finish(status="DONE", completion=3)
        self.assertEqual(store.snapshot.latest, 3)
        self.assertEqual(store.snapshot.result, 3)
        self.assertEqual(store.snapshot.integrity, Integrity.DEGRADED)
        self.assertEqual(publisher.frames[:2], [b"tick:1", b"tick:3"])

    def test_publish_cannot_close_before_the_outer_event_is_buffered(self):
        for operation in ("finish", "close_for_retry", "announce_replacement"):
            with self.subTest(operation=operation):
                runtime, publisher, store = self.make_runtime()
                callbacks = {
                    "finish": lambda: runtime.finish(status="DONE", completion=1),
                    "close_for_retry": runtime.close_for_retry,
                    "announce_replacement": lambda: runtime.announce_replacement(publisher),
                }
                publisher.callback = callbacks[operation]

                self.assert_reentrant(lambda: runtime.send(1))

                self.assertFalse(runtime.closed)
                self.assertEqual(runtime.pending_count, 0)
                self.assertIsNone(store.snapshot.terminal)
                self.assertEqual(publisher.frames, [b"tick:1"])

    def test_commit_cannot_drop_a_reentrant_send_from_the_frozen_tail(self):
        runtime, publisher, store = self.make_runtime()
        runtime.send(1)
        store.commit_callback = lambda: runtime.send(2)

        self.assert_reentrant(lambda: runtime.finish(status="DONE", completion=1))

        self.assertFalse(runtime.closed)
        self.assertEqual(runtime.pending_count, 1)
        self.assertEqual(publisher.frames, [b"tick:1"])
        self.assertIsNone(store.snapshot.result)
        store.commit_callback = None
        runtime.finish(status="DONE", completion=1)
        self.assertEqual(store.snapshot.latest, 1)
        self.assertEqual(store.snapshot.integrity, Integrity.DEGRADED)
        self.assertTrue(runtime.closed)

    def test_checkpoint_reentrancy_is_not_a_recoverable_archive_failure(self):
        runtime, publisher, store = self.make_runtime(policy=StreamPolicy(checkpoint_event_count=1))
        store.checkpoint_callback = lambda: runtime.send(2)

        self.assert_reentrant(lambda: runtime.send(1))

        self.assertEqual(runtime.pending_count, 1)
        self.assertEqual(publisher.frames, [b"tick:1"])
        self.assertEqual(runtime.archive_status, Integrity.DEGRADED)
        store.checkpoint_callback = None
        runtime.send(3)
        self.assertEqual(store.snapshot.latest, 3)
        self.assertEqual(runtime.pending_count, 0)

    def test_capacity_flush_and_activity_callbacks_cannot_mutate_the_runtime(self):
        policies = (
            StreamPolicy(max_archive_bytes=8, checkpoint_event_count=1000, activity_interval_seconds=999999),
            StreamPolicy(max_events=0, activity_interval_seconds=0),
        )
        for policy in policies:
            with self.subTest(policy=policy):
                runtime, publisher, store = self.make_runtime(policy=policy)
                if policy.max_archive_bytes == 8:
                    runtime.send(1)
                    store.checkpoint_callback = lambda: runtime.send(9)
                else:
                    store.touch_callback = lambda: runtime.send(9)

                self.assert_reentrant(lambda: runtime.send(2))

                self.assertNotIn(b"tick:9", publisher.frames)
                self.assertFalse(runtime.closed)

    def test_retry_close_does_not_swallow_reentrancy_or_freeze_a_false_summary(self):
        runtime, publisher, store = self.make_runtime()
        runtime.send(1)
        store.checkpoint_callback = lambda: runtime.send(2)

        self.assert_reentrant(runtime.close_for_retry)

        self.assertFalse(runtime.closed)
        self.assertEqual(runtime.pending_count, 1)
        self.assertEqual(runtime.archive_status, Integrity.DEGRADED)
        store.checkpoint_callback = None
        runtime.close_for_retry()
        self.assertTrue(runtime.closed)
        self.assertEqual(store.snapshot.latest, 1)

    def test_replacement_callback_cannot_write_the_announcing_runtime(self):
        runtime, publisher, store = self.make_runtime()

        class PreviousPublisher:
            def publish(self, event):
                runtime.send(2)
                return event

        self.assert_reentrant(lambda: runtime.announce_replacement(PreviousPublisher()))
        self.assertEqual(publisher.frames, [])
        self.assertEqual(runtime.archive_status, Integrity.DEGRADED)
        self.assertTrue(runtime.announce_replacement(publisher))

    def test_read_only_port_callbacks_and_recovery_after_an_exception_are_supported(self):
        runtime, publisher, store = self.make_runtime()
        observed = []
        publisher.callback = lambda: observed.append(
            (runtime.closed, runtime.pending_count, runtime.archive_status, runtime.binding, runtime.policy)
        )
        runtime.send(1)
        self.assertEqual(observed[0][:3], (False, 0, Integrity.COMPLETE))
        self.assertEqual(observed[0][3], runtime.binding)
        self.assertIsInstance(observed[0][4], StreamPolicy)
        publisher.callback = lambda: (_ for _ in ()).throw(LiveUnavailable())
        runtime.send(2)
        publisher.callback = None
        runtime.send(3)
        runtime.finish(status="DONE", completion=6)
        self.assertEqual(store.snapshot.latest, 3)
        self.assertTrue(runtime.closed)

    def test_all_six_recoverable_catches_propagate_reentry_and_persist_integrity(self):
        cases = (
            "publish",
            "replacement",
            "retry_checkpoint",
            "capacity_checkpoint",
            "automatic_checkpoint",
            "activity",
        )
        for case in cases:
            with self.subTest(case=case):
                summaries = []
                policy = StreamPolicy(
                    max_events=0 if case == "activity" else 1000,
                    max_archive_bytes=8 if case == "capacity_checkpoint" else 1024,
                    checkpoint_event_count=1 if case == "automatic_checkpoint" else 1000,
                    activity_interval_seconds=0 if case == "activity" else 999999,
                )
                runtime, publisher, store = self.make_runtime(policy=policy, after_close=summaries.append)
                partial_effects = []

                def callback():
                    partial_effects.append(case)
                    if "checkpoint" in case:
                        # 模拟存储已处理批次后才由回调拒绝重入，不能宣称仍是 COMPLETE。
                        MemorySnapshotStore.checkpoint(
                            store,
                            binding=runtime.binding,
                            events=(CounterCodec().business(8),),
                            archive_status=runtime.archive_status,
                        )
                    runtime.send(9)

                if case in ("retry_checkpoint", "capacity_checkpoint"):
                    runtime.send(1)
                if "checkpoint" in case:
                    store.checkpoint_callback = callback
                elif case == "activity":
                    store.touch_callback = callback
                elif case == "publish":
                    publisher.callback = callback

                class PreviousPublisher:
                    def publish(self, event):
                        callback()
                        return event

                if case == "replacement":

                    def operation():
                        runtime.announce_replacement(PreviousPublisher())

                elif case == "retry_checkpoint":
                    operation = runtime.close_for_retry
                else:

                    def operation():
                        runtime.send(2)

                self.assert_reentrant(operation)
                self.assertEqual(partial_effects, [case])
                if "checkpoint" in case:
                    self.assertEqual(store.snapshot.latest, 8)
                self.assertNotIn(b"tick:9", publisher.frames)
                self.assertFalse(runtime.closed)
                expected = Integrity.TRUNCATED if case == "activity" else Integrity.DEGRADED
                self.assertEqual(runtime.archive_status, expected)
                runtime.send(3)
                runtime.finish(status="DONE", completion=3)
                self.assertEqual(store.snapshot.integrity, expected)
                self.assertEqual(store.snapshot.result, 3)
                self.assertTrue(summaries[0].degraded)

    def test_archive_encoding_reentry_after_publication_marks_later_commit_degraded(self):
        class CallbackCodec(CounterCodec):
            callback = None

            def encode(self, event, target):
                if target == EncodingTarget.ARCHIVE and self.callback is not None:
                    callback, self.callback = self.callback, None
                    callback()
                return super().encode(event, target)

        codec = CallbackCodec()
        runtime, publisher, store = self.make_runtime(codec=codec)
        codec.callback = lambda: runtime.send(9)
        self.assert_reentrant(lambda: runtime.send(1))
        self.assertEqual(publisher.frames, [b"tick:1"])
        self.assertEqual(runtime.pending_count, 0)
        self.assertEqual(runtime.archive_status, Integrity.DEGRADED)
        runtime.send(3)
        runtime.finish(status="DONE", completion=3)
        self.assertEqual(store.snapshot.latest, 3)
        self.assertEqual(store.snapshot.integrity, Integrity.DEGRADED)

    def test_reentry_before_port_io_does_not_degrade_even_after_an_earlier_success(self):
        for source in ("policy", "business", "live_encoding"):
            with self.subTest(source=source):

                class CallbackCodec(CounterCodec):
                    business_callback = None
                    encode_callback = None

                    def business(self, data):
                        if self.business_callback:
                            callback, self.business_callback = self.business_callback, None
                            callback()
                        return super().business(data)

                    def encode(self, event, target):
                        if self.encode_callback:
                            callback, self.encode_callback = self.encode_callback, None
                            callback()
                        return super().encode(event, target)

                policy_callback = []

                def policy():
                    if policy_callback:
                        policy_callback.pop()()
                    return StreamPolicy(checkpoint_event_count=1000)

                codec = CallbackCodec()
                runtime, publisher, store = self.make_runtime(policy=policy, codec=codec)
                runtime.send(1)

                def callback():
                    runtime.send(9)

                if source == "policy":
                    policy_callback.append(callback)
                elif source == "business":
                    codec.business_callback = callback
                else:
                    codec.encode_callback = callback
                self.assert_reentrant(lambda: runtime.send(2))
                self.assertEqual(runtime.archive_status, Integrity.COMPLETE)
                self.assertEqual(publisher.frames, [b"tick:1"])
                runtime.send(3)
                runtime.finish(status="DONE", completion=4)
                self.assertEqual(store.snapshot.integrity, Integrity.COMPLETE)
                self.assertEqual(store.snapshot.latest, 3)

    def test_host_may_handle_rejected_inner_write_without_degrading_outer_success(self):
        runtime, publisher, store = self.make_runtime()
        publisher.callback = lambda: self.assert_reentrant(lambda: runtime.send(9))
        runtime.send(1)
        self.assertEqual(runtime.archive_status, Integrity.COMPLETE)
        self.assertEqual(runtime.pending_count, 1)
        runtime.finish(status="DONE", completion=1)
        self.assertEqual(store.snapshot.latest, 1)
        self.assertEqual(store.snapshot.integrity, Integrity.COMPLETE)

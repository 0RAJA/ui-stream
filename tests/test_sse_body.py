"""Synchronous SSE bodies own one reader iterator, never a shared producer."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from demos.sqlite import events as demo_events
from ui_stream import sse
from ui_stream.codec import Event


class Source:
    def __init__(self, values=(), *, close_error=None):
        self.values = iter(values)
        self.next_count = 0
        self.close_count = 0
        self.close_error = close_error

    def __iter__(self):
        return self

    def __next__(self):
        self.next_count += 1
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self):
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


class SSEBodyTest(unittest.TestCase):
    def body(self, events, **options):
        body = getattr(sse, "SSEBody", None)
        self.assertTrue(callable(body), "SSEBody must implement synchronous iterator ownership")
        return body(events, **options)

    def test_close_before_first_next_is_forwarded_without_advancing_source(self):
        source = Source([Event(data=4)])
        body = self.body(source)
        self.assertIs(iter(body), body)
        self.assertEqual(source.next_count, 0)
        body.close()
        body.close()
        self.assertEqual(source.close_count, 1)
        self.assertEqual(list(body), [])
        self.assertEqual(source.next_count, 0)

    def test_default_encoding_includes_heartbeat_and_closes_on_exhaustion(self):
        source = Source([Event(stream_id="1-0", data=4), None])
        body = self.body(source)
        self.assertEqual(list(body), [b"id: 1-0\ndata: 4\n\n", b": heartbeat\n\n"])
        self.assertEqual(source.close_count, 1)
        body.close()
        self.assertEqual(source.close_count, 1)

    def test_injected_encoder_receives_each_item_without_prefetch(self):
        source = Source([4, None, 9])
        encoded = []

        def encode(value):
            encoded.append(value)
            return b"heartbeat" if value is None else str(value).encode("ascii")

        body = self.body(source, encoder=encode)
        self.assertEqual(next(body), b"4")
        self.assertEqual((source.next_count, encoded), (1, [4]))
        self.assertEqual(list(body), [b"heartbeat", b"9"])
        self.assertEqual(encoded, [4, None, 9])
        self.assertEqual(source.close_count, 1)

    def test_source_without_close_is_a_valid_synchronous_iterator(self):
        body = self.body(iter([4]), encoder=lambda value: str(value).encode("ascii"))
        self.assertEqual(list(body), [b"4"])
        body.close()
        self.assertEqual(list(body), [])

    def test_source_failure_closes_once_and_preserves_exception(self):
        failure = ValueError("synthetic source failure")
        source = Source([failure])
        body = self.body(source)
        with self.assertRaises(ValueError) as raised:
            next(body)
        self.assertIs(raised.exception, failure)
        self.assertEqual(source.close_count, 1)
        body.close()
        self.assertEqual(source.close_count, 1)
        self.assertEqual(list(body), [])

    def test_encoder_failure_closes_once_without_synthesizing_a_frame(self):
        failure = ValueError("synthetic encoding failure")
        source = Source([4, 9])

        def encode(value):
            raise failure

        body = self.body(source, encoder=encode)
        with self.assertRaises(ValueError) as raised:
            next(body)
        self.assertIs(raised.exception, failure)
        self.assertEqual((source.next_count, source.close_count), (1, 1))
        self.assertEqual(list(body), [])

    def test_cleanup_failure_does_not_replace_primary_read_or_encode_failure(self):
        failure = ValueError("synthetic primary")
        for source_failure in (True, False):
            with self.subTest(source_failure=source_failure):
                source = Source([failure] if source_failure else [4], close_error=OSError("synthetic hidden detail"))

                def encode(value):
                    raise failure

                body = self.body(source, encoder=encode)
                with self.assertLogs("ui_stream.sse", level="WARNING") as logged:
                    with self.assertRaises(ValueError) as raised:
                        next(body)
                self.assertIs(raised.exception, failure)
                self.assertEqual(source.close_count, 1)
                self.assertNotIn("hidden detail", "\n".join(logged.output))
                body.close()
                self.assertEqual(source.close_count, 1)

    def test_encoder_stop_iteration_is_a_contract_error_not_silent_eof(self):
        source = Source([4, 9])
        failure = StopIteration("synthetic encoder misuse")

        def encode(value):
            raise failure

        body = self.body(source, encoder=encode)
        with self.assertRaises(Exception) as raised:
            next(body)
        self.assertIsInstance(raised.exception, RuntimeError)
        self.assertIs(raised.exception.__cause__, failure)
        self.assertEqual((source.next_count, source.close_count), (1, 1))
        self.assertEqual(list(body), [])

    def test_demo_encoder_uses_body_with_its_own_event_and_cursor_protocol(self):
        message = getattr(demo_events, "DemoSSEMessage", None)
        encode = getattr(demo_events, "encode_sse_message", None)
        self.assertTrue(callable(message) and callable(encode), "Demo needs its own SSE message encoder")
        source = Source([message("progress", {"step": 4}, 7), None])
        body = self.body(source, encoder=encode)
        self.assertEqual(list(body), [b'event: progress\nid: 7\ndata: {"step":4}\n\n', b": heartbeat\n\n"])
        self.assertEqual(source.close_count, 1)

    def test_explicit_close_error_propagates_once_and_body_stays_closed(self):
        failure = OSError("synthetic close failure")
        source = Source([4], close_error=failure)
        body = self.body(source)
        with self.assertRaises(OSError) as raised:
            body.close()
        self.assertIs(raised.exception, failure)
        body.close()
        self.assertEqual((source.next_count, source.close_count), (0, 1))
        self.assertEqual(list(body), [])

    def test_close_error_after_normal_exhaustion_is_visible(self):
        source = Source(close_error=OSError("synthetic close failure"))
        body = self.body(source)
        with self.assertRaises(OSError):
            next(body)
        self.assertEqual(source.close_count, 1)
        self.assertEqual(list(body), [])

    def test_close_stop_iteration_after_normal_exhaustion_is_not_silent_eof(self):
        failure = StopIteration("synthetic source close failure")
        source = Source(close_error=failure)
        body = self.body(source)
        with self.assertRaises(RuntimeError) as raised:
            list(body)
        self.assertIs(raised.exception.__cause__, failure)
        self.assertEqual(source.close_count, 1)
        body.close()
        self.assertEqual(source.close_count, 1)
        self.assertEqual(list(body), [])

    def test_explicit_close_stop_iteration_propagates_unchanged(self):
        failure = StopIteration("synthetic explicit close failure")
        source = Source([4], close_error=failure)
        body = self.body(source)
        with self.assertRaises(StopIteration) as raised:
            body.close()
        self.assertIs(raised.exception, failure)
        self.assertEqual((source.next_count, source.close_count), (0, 1))
        body.close()
        self.assertEqual(source.close_count, 1)

    def test_non_bytes_encoder_result_is_rejected_and_source_is_closed(self):
        source = Source([4])
        body = self.body(source, encoder=lambda value: "not bytes")
        with self.assertRaises(TypeError):
            next(body)
        self.assertEqual(source.close_count, 1)
        self.assertEqual(list(body), [])

    def test_interrupts_are_reraised_after_closing_the_source(self):
        failure = KeyboardInterrupt()
        source = Source([failure])
        body = self.body(source)
        with self.assertRaises(KeyboardInterrupt) as raised:
            next(body)
        self.assertIs(raised.exception, failure)
        self.assertEqual(source.close_count, 1)

    def test_iterable_owner_is_not_implicitly_acquired_or_closed(self):
        class Owner:
            def __init__(self):
                self.source = Source([4])
                self.iter_count = 0
                self.close_count = 0

            def __iter__(self):
                self.iter_count += 1
                return self.source

            def close(self):
                self.close_count += 1

        owner = Owner()
        with self.assertRaises(TypeError):
            self.body(owner)
        self.assertEqual((owner.iter_count, owner.close_count), (0, 0))
        body = self.body(iter(owner), encoder=lambda value: str(value).encode("ascii"))
        self.assertEqual(list(body), [b"4"])
        self.assertEqual((owner.source.close_count, owner.close_count), (1, 0))

    def test_async_iterator_is_rejected_without_starting_it(self):
        class AsyncSource:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise AssertionError("async source must not start")

        with self.assertRaises(TypeError):
            self.body(AsyncSource())

    def test_standard_library_host_can_use_sse_body_without_json_extra(self):
        self.assertTrue(callable(getattr(sse, "SSEBody", None)), "SSEBody must be available")
        package = os.environ.get("UI_STREAM_TEST_PATH", str(Path(__file__).resolve().parents[1] / "src"))
        project = str(Path(__file__).resolve().parents[1])
        script = (
            "import sys; sys.path[:0]=" + repr([package, project]) + "; "
            "from ui_stream.sse import SSEBody; from ui_stream.types import EncodingTarget; "
            "from examples.counter import CounterCodec; "
            "codec=CounterCodec(); body=SSEBody(iter([codec.business(4)]), "
            "encoder=lambda e:codec.encode(e,EncodingTarget.LIVE)); "
            "assert list(body)==[b'tick:4']; assert 'pydantic' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

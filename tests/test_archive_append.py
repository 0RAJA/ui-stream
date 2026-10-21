"""Archive retries deduplicate host identities before bounded prefix fitting."""

import unittest
from dataclasses import dataclass

from ui_stream import archive
from ui_stream.codec import Event


@dataclass(frozen=True)
class Record:
    identity: str | int | None
    payload: bytes


def identity(event):
    return event.identity


def size(event):
    return len(event.payload)


class ArchiveAppendTest(unittest.TestCase):
    def append(self, existing, incoming, *, max_bytes=1024, **options):
        append = getattr(archive, "append_archive_events", None)
        self.assertTrue(callable(append), "archive must combine identity deduplication and prefix fitting")
        return append(
            existing,
            incoming,
            max_bytes=max_bytes,
            identity=options.pop("identity", identity),
            event_size=options.pop("event_size", size),
            **options
        )

    def test_existing_records_are_preserved_and_incoming_first_identity_wins(self):
        old = Record("a", b"old")
        retained = Record("b", b"first")
        existing = [old, old]
        incoming = [Record("a", b"changed"), retained, Record("b", b"conflict")]
        result = self.append(existing, incoming)
        self.assertEqual(result.events, [old, old, retained])
        self.assertEqual((result.appended_count, result.duplicate_count, result.dropped_count), (1, 2, 0))
        self.assertFalse(result.truncated)
        self.assertEqual(existing, [old, old])
        self.assertEqual(len(incoming), 3)
        self.assertIsNot(result.events, existing)

    def test_equal_bodies_with_distinct_or_absent_identities_are_not_deduplicated(self):
        incoming = [Record("a", b"x"), Record("b", b"x"), Record(None, b"x"), Record(None, b"x")]
        result = self.append([], incoming)
        self.assertEqual(result.events, incoming)
        self.assertEqual((result.appended_count, result.duplicate_count, result.dropped_count), (4, 0, 0))

    def test_zero_and_empty_text_are_real_identities(self):
        incoming = [Record(0, b"a"), Record("", b"b"), Record(0, b"c"), Record("", b"d")]
        result = self.append([], incoming)
        self.assertEqual(result.events, incoming[:2])
        self.assertEqual((result.appended_count, result.duplicate_count), (2, 2))

    def test_capacity_keeps_new_unique_prefix_and_counts_loss_separately(self):
        first = Record("a", b"123")
        incoming = [first, Record("a", b"123"), Record("b", b"too large"), Record("b", b"again"), Record(None, b"x")]
        result = self.append([], incoming, max_bytes=8)
        self.assertEqual(result.events, [first])
        self.assertEqual((result.appended_count, result.duplicate_count, result.dropped_count), (1, 2, 2))
        self.assertTrue(result.truncated)

    def test_exact_array_limit_includes_existing_event_and_comma(self):
        old, new = Record("a", b"a"), Record("b", b"b")
        exact = self.append([old], [new], max_bytes=5)
        self.assertEqual(exact.events, [old, new])
        self.assertEqual((exact.appended_count, exact.dropped_count, exact.truncated), (1, 0, False))
        short = self.append([old], [new], max_bytes=4)
        self.assertEqual(short.events, [old])
        self.assertEqual((short.appended_count, short.dropped_count, short.truncated), (0, 1, True))

    def test_all_duplicates_do_not_report_new_loss_when_existing_is_oversized(self):
        old = Record("a", b"large")
        result = self.append([old], [old, old], max_bytes=1)
        self.assertEqual(result.events, [old])
        self.assertEqual((result.appended_count, result.duplicate_count, result.dropped_count), (0, 2, 0))
        self.assertFalse(result.truncated)

    def test_empty_incoming_copies_existing_without_calling_callbacks(self):
        def unused(event):
            raise AssertionError("no incoming requires no measurements or identity scan")

        old = Record("a", b"large")
        result = self.append([old], [], max_bytes=1, identity=unused, event_size=unused)
        self.assertEqual(result.events, [old])
        self.assertEqual((result.appended_count, result.duplicate_count, result.dropped_count), (0, 0, 0))
        self.assertFalse(result.truncated)

    def test_callbacks_scan_identities_once_and_measure_only_retained_candidates(self):
        old, new = Record("a", b"a"), Record("b", b"b")
        identities, measured = [], []

        def track_identity(event):
            identities.append(event)
            return event.identity

        def track_size(event):
            measured.append(event)
            return len(event.payload)

        result = self.append([old], [old, new, new], identity=track_identity, event_size=track_size)
        self.assertEqual(result.events, [old, new])
        self.assertEqual(identities, [old, old, new, new])
        self.assertEqual(measured, [old, new])

    def test_callback_errors_propagate_without_mutating_input(self):
        old, new = Record("a", b"a"), Record("b", b"b")
        failure = ValueError("synthetic record contract")

        def invalid(event):
            raise failure

        for options in ({"identity": invalid}, {"event_size": invalid}):
            with self.subTest(options=list(options)):
                with self.assertRaises(ValueError) as raised:
                    self.append([old], [new], **options)
                self.assertIs(raised.exception, failure)
        with self.assertRaises(TypeError):
            self.append([], [new], identity=lambda event: [])
        self.assertEqual((old.payload, new.payload), (b"a", b"b"))

    def test_host_hidden_identity_and_unicode_count_toward_persisted_capacity(self):
        old = Record("id1", "中".encode("utf-8"))

        def persisted_size(event):
            return len(b'{"id":"' + event.identity.encode("ascii") + b'","value":"' + event.payload + b'"}')

        exact_bytes = len('[{"id":"id1","value":"中"}]'.encode("utf-8"))
        exact = self.append([], [old, old], max_bytes=exact_bytes, event_size=persisted_size)
        self.assertEqual(
            (exact.events, exact.appended_count, exact.duplicate_count, exact.dropped_count), ([old], 1, 1, 0)
        )
        short = self.append([], [old], max_bytes=exact_bytes - 1, event_size=persisted_size)
        self.assertEqual((short.events, short.appended_count, short.dropped_count), ([], 0, 1))

    def test_default_json_measurement_and_identity_can_be_used_together(self):
        old, new = Event(stream_id="1-0", data="A"), Event(stream_id="2-0", data="B")
        result = self.append([old], [old, new], identity=lambda event: event.stream_id, event_size=None)
        self.assertEqual(result.events, [old, new])
        self.assertEqual((result.appended_count, result.duplicate_count, result.dropped_count), (1, 1, 0))

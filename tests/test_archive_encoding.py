"""Archive capacity follows persisted bytes, including host-owned hidden identities."""

import json
import unittest

from pydantic import Field

from ui_stream import archive
from ui_stream.codec import Event


class ArchivedEvent(Event):
    archive_id: str = Field(exclude=True)


def archive_event_size(event):
    payload = event.model_dump(mode="json")
    payload["archive_id"] = event.archive_id
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class ArchiveEncodingTest(unittest.TestCase):
    def test_public_default_measurement_matches_compact_utf8_json_array(self):
        events = [Event(data="中文"), Event(stream_id="2-0", data={"escaped": '"\\', "value": None})]
        expected = json.dumps(
            [event.model_dump(mode="json") for event in events], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

        self.assertEqual(archive.encoded_archive_bytes([]), 2)
        self.assertEqual(archive.encoded_archive_bytes(events), len(expected))

    def test_hidden_archive_identity_counts_toward_exact_capacity(self):
        event = ArchivedEvent(stream_id="1-0", data="中", archive_id="00000000-0000-0000-0000-000000000001")
        persisted = (
            '[{"event":null,"stream_id":"1-0","data":"中",' '"archive_id":"00000000-0000-0000-0000-000000000001"}]'
        ).encode("utf-8")

        self.assertEqual(archive.encoded_archive_bytes([event], event_size=archive_event_size), len(persisted))
        self.assertLess(archive.encoded_archive_bytes([event]), len(persisted))
        self.assertEqual(
            archive.fit_archive_events(
                existing=[], incoming=[event], max_bytes=len(persisted), event_size=archive_event_size
            ),
            ([event], 1, False),
        )
        self.assertEqual(
            archive.fit_archive_events(
                existing=[], incoming=[event], max_bytes=len(persisted) - 1, event_size=archive_event_size
            ),
            ([], 0, True),
        )

    def test_existing_hidden_id_and_array_comma_both_consume_capacity(self):
        existing = ArchivedEvent(data="A", archive_id="00000000-0000-0000-0000-000000000001")
        incoming = ArchivedEvent(data="B", archive_id="00000000-0000-0000-0000-000000000002")
        payloads = [
            {"event": None, "stream_id": None, "data": "A", "archive_id": existing.archive_id},
            {"event": None, "stream_id": None, "data": "B", "archive_id": incoming.archive_id},
        ]
        exact_bytes = len(json.dumps(payloads, separators=(",", ":")).encode("utf-8"))

        self.assertEqual(
            archive.fit_archive_events(
                existing=[existing], incoming=[incoming], max_bytes=exact_bytes, event_size=archive_event_size
            ),
            ([existing, incoming], 1, False),
        )
        self.assertEqual(
            archive.fit_archive_events(
                existing=[existing], incoming=[incoming], max_bytes=exact_bytes - 1, event_size=archive_event_size
            ),
            ([existing], 0, True),
        )

    def test_capacity_stops_at_first_rejected_event_without_skipping_ahead(self):
        incoming = [b"123", b"too large", b"x"]
        self.assertEqual(
            archive.fit_archive_events(existing=[], incoming=incoming, max_bytes=8, event_size=len),
            ([b"123"], 1, True),
        )

    def test_custom_event_measurement_is_linear_for_existing_and_incoming(self):
        measured = []

        def event_size(event):
            measured.append(event)
            return len(event)

        existing, incoming = [b"a"] * 5000, [b"b"] * 50
        merged, accepted, truncated = archive.fit_archive_events(
            existing=existing, incoming=incoming, max_bytes=1024 * 1024, event_size=event_size
        )

        self.assertEqual((merged, accepted, truncated), (existing + incoming, 50, False))
        self.assertEqual(measured, existing + incoming)

    def test_no_incoming_keeps_existing_even_when_capacity_is_already_exceeded(self):
        existing = [b"large"]
        self.assertEqual(
            archive.fit_archive_events(existing=existing, incoming=[], max_bytes=1, event_size=len),
            (existing, 0, False),
        )
        self.assertEqual(
            archive.fit_archive_events(existing=existing, incoming=[b"a"], max_bytes=1, event_size=len),
            (existing, 0, True),
        )

    def test_default_fit_remains_compatible_with_model_dump_events(self):
        first, second = Event(data="A"), Event(data="B")
        encoded = json.dumps(
            [first.model_dump(mode="json"), second.model_dump(mode="json")], separators=(",", ":")
        ).encode("utf-8")

        self.assertEqual(
            archive.fit_archive_events(existing=[first], incoming=[second], max_bytes=len(encoded)),
            ([first, second], 1, False),
        )
        self.assertEqual(
            archive.fit_archive_events(existing=[first], incoming=[second], max_bytes=len(encoded) - 1),
            ([first], 0, True),
        )

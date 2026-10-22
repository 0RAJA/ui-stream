# Migration

[简体中文](migration.md) · [Documentation](README.en.md)

## Upgrade from a3 to a4

- Replace `finish_retry()` with `close_for_retry()`. There is no compatibility alias. It still only attempts a checkpoint and closes; it does not commit a final result, reschedule computation, or acknowledge durable persistence.
- Remove same-instance writes from codec/policy/port callbacks. Move those writes back into the host's task sequence. Before commit, while the instance is open, reentry raises `ReentrantStreamOperation`, not a backend outage to retry blindly. If the error escapes after a writing port is entered, DEGRADED is recorded and preserved by later commits. Rejection before I/O or an inner rejection handled by the host does not cause degradation. Same-thread property reads remain supported. A port holding the lock must not wait for another thread to read lock-protected properties or invoke methods needing that lock. Writes from post-commit terminal callbacks encounter the closed error first and the outer call keeps notification-failure semantics; see [API](api.en.md).
- Policies accept only non-negative built-in int values, excluding int subclasses such as IntEnum. Normalize external configuration explicitly; bools, floats, strings, and negative values are rejected before event I/O. Zero keeps its field-specific meaning, including per-event checkpoint/activity checks.
- With `state_check`, the first iteration checks state before reading live data. Later checks keep their configured interval. The first check adds one host callback invocation; keep it lightweight. Initial reset/errors expose no old live event; an initial terminal still drains the tail before emitting a synthetic end. Authorize before constructing the HTTP response.
- `Binding`, `Checkpoint`, `EncodingTarget`, and the assembly Protocols are also exported by `ui_stream`. Existing `ui_stream.types` imports remain valid.

See [API](api.en.md) for the current failure and zero-value contracts. Run your own adapter/consumer tests when upgrading.

## Upgrade a2 to a3

Upgrading from `0.1.0a2` to `0.1.0a3` requires updating custom codecs and checking integration of previous-stream notifications, archive appends, and SSE lifetime. Pin the version, update adapters, and run contract tests before upgrading.

### Codecs must implement reset

`EventCodec` now requires `business(data)`, `terminal(status)`, `reset(reason)`, and `encode(event, target)`. Add reset to custom codecs; Runtime does not probe older protocols. Default `JsonEventCodec.reset(reason)` creates a cursor-free `STREAM_RESET` with `{"reason": reason}` data. Construction/encoding errors propagate. Reuse `codec.terminal(status)` for existing terminal messages.

[Counter](../examples/counter.py) and [DemoCodec](../demos/sqlite/events.py) show custom protocols. The a2 fixed/dynamic policy rules are unchanged.

### Notify the previous channel after execution rotation

The host first commits durable execution rotation and fences the old writer. Then call `announce_replacement(previous_publisher)` on the new Runtime, passing the previous execution's publisher. This example only demonstrates memory ports; the host implements database transactions and fencing.

```python
from examples.counter import make_counter

previous, old_channel, old_store = make_counter()
runtime, channel, store = make_counter()
assert runtime.announce_replacement(old_channel)
assert old_channel.frames == [b"reset:execution_replaced"]
runtime.send(4)
runtime.finish(status="DONE", completion=4)
assert store.snapshot.result == 4
```

The new Runtime's codec constructs `reset("execution_replaced")` and validates LIVE encoding. Except for `ReentrantStreamOperation`, which propagates, only `live_errors` raised during the previous publisher's publication degrade: return False, mark the new Runtime DEGRADED, and log identities/error type without a body. Other failures propagate; True only means publish returned normally. This call consumes no budgets, archives nothing, closes no execution, and does not retry. Host state checks still cover readers that miss the notification. Both ends must use matching event protocols.

### Append archives using host identities

```python
from ui_stream.archive import append_archive_events
from ui_stream.codec import Event

first = Event(stream_id="1-0", data="A")
second = Event(stream_id="2-0", data="B")
result = append_archive_events(
    [first], [first, second], max_bytes=1024, identity=lambda event: event.stream_id
)
assert result.events == [first, second]
assert (result.appended_count, result.duplicate_count, result.dropped_count) == (1, 1, 0)
assert not result.truncated
```

`append_archive_events` accepts finite sequences, deduplicates by `identity(event)`, then reuses `fit_archive_events` to retain the fitting contiguous prefix. Existing events remain unchanged; the first event for an ID wins. Only None means no identity; identity-free events remain eligible without body-based deduplication. The example uses receipt-shaped IDs; production retry identities must be durably saved and immutable in the host. Runtime generates no IDs.

The result contains a new `events` list, `appended_count`, `duplicate_count`, and capacity `dropped_count`. The counts sum to incoming length; `truncated` means capacity loss. Deduplication precedes fitting, so later occurrences of a capacity-rejected first ID still count as duplicates. Oversized existing data is retained; the first rejected candidate prevents accepting later smaller ones. The host owns transactions and `event_size` matching actual persistence, including fields hidden from public serialization.

### Let the HTTP layer hold a synchronous SSEBody

```python
from ui_stream.sse import SSEBody, encode_sse_heartbeat


def encode_update(value):
    return encode_sse_heartbeat() if value is None else f"data: {value}\n\n".encode()


body = SSEBody(iter([4, None]), encoder=encode_update)
try:
    assert list(body) == [b"data: 4\n\n", b": heartbeat\n\n"]
finally:
    body.close()
```

Replace encoding-only generator wrappers with `SSEBody(events, encoder=...)`. Supply a synchronous Iterator; explicitly call iter for an Iterable. The default encoder handles default Event/None; a custom encoder receives every source item and returns bytes. SSEBody itself uses only the standard library, so custom protocols need no Pydantic. HTTP frameworks should call body.close on disconnect and exit.

Closing before the first next forwards close to the source iterator. Exhaustion and read/encoding errors also close it, at most once. It does not own an outer Iterable, shared client, channel, or producer task. Direct close errors propagate. While handling a read/encoding failure, ordinary cleanup errors only log their type and preserve the original error. StopIteration from the encoder or exhaustion cleanup becomes RuntimeError with its cause to avoid false normal exhaustion; explicit close preserves the original exception. An unstarted source generator's finally follows Python rules; close cannot interrupt a blocking next. Concurrent next/close and async iterators are unsupported.

See [API](api.en.md) for default Redis integration and the Demo's [HTTP boundary](../demos/sqlite/app.py) for a custom integer cursor. Cursor-source priority and HTTP response headers stay with the host.

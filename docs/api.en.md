# API: assembly, calls, and failures

[简体中文](api.md) · [Documentation](README.en.md)

Read [Architecture and concepts](architecture.en.md) first, then integrate through this lifecycle. [types.py](../src/ui_stream/types.py) and [runtime.py](../src/ui_stream/runtime.py) define signatures; underscore-prefixed names are private.

## 1. Create a Runtime

The host creates an execution, then passes its identity and three ports to `StreamRuntime`. See the complete [Counter](../examples/counter.py); for durable transactions and UI reads, see the [SQLite Demo](../demos/sqlite/README.en.md).

| Constructor argument | Supply and purpose |
| --- | --- |
| `binding` | `Binding(resource_id, task_id, execution_id)`, or the same read-only attributes. Every storage call carries it for host ownership checks. |
| `codec` | Implements `business`, `terminal`, `reset`, `encode`, defining input, event structure, terminal vocabulary, and byte representation. |
| `publisher` | Implements synchronous `publish(event)`, returning the event for archiving, optionally with a real message-ID receipt. |
| `archive_store` | Implements `checkpoint`, `touch_activity`, `commit` to save progress and the final business result. |
| `policy` / `archive_status` | A fixed StreamPolicy or a no-argument factory returning one; plus existing execution integrity. See per-send semantics below. |
| `live_errors` / `archive_errors` | Exception types allowed to degrade and continue. Defaults are `(LiveUnavailable, OSError)` and `(ArchiveUnavailable,)`; final commit errors still propagate. |
| `after_close` / `closed_error` | Optional close observer. Using `send`/`finish` after closure raises `ClosedStream` by default; custom types must support no-argument construction. |

Common assembly types are available at the package entry point; `ui_stream.types` retains the original definitions and import paths:

```python
from ui_stream import (
    ArchiveStore, Binding, Checkpoint, EncodingTarget, EventCodec,
    ExecutionBinding, Integrity, LivePublisher, StreamPolicy, StreamRuntime,
)
```

Initialization stores ports and configuration; it creates no durable execution and performs no backend I/O. The host creates/closes clients; Runtime has no general client-close method.

Writes on the same instance must not reenter: codecs, policy factories, and port callbacks must not drive another write on this Runtime. Before commit, while the instance remains open, nested writes raise `ReentrantStreamOperation` even when backend error configuration includes `Exception`.

If a reentry error escapes an outer operation after entering a potentially writing port, Runtime records DEGRADED and still propagates it; a later successful finish preserves that status. Reentry before I/O in the policy/initial encoding does not degrade, nor does a host-handled inner rejection followed by a successful outer operation.

DEGRADED records an observed failure or uncertainty about completeness, not confirmed data loss; this includes reentry inside the final commit. Runtime cannot determine whether a callback ran before or after the transaction committed. Ordinary commit errors still propagate and retain the open state without additional degradation.

Same-thread callbacks can read properties through the RLock. A port holding the lock must not block waiting for another thread to call a method or property requiring that lock. Reading `pending_count`, `closed`, or `archive_status` also acquires it, so waiting for those reads can deadlock too.

After a successful final commit, Runtime closes before publishing the terminal. A terminal publisher callback calling `send` therefore raises the closed error first (`ClosedStream` by default). The outer `finish` treats it as a post-commit notification failure, returns normally, and records DEGRADED without propagating a pre-commit reentry error or undoing the result.

## 2. Send progress and finish

| Method | Input and output | Call when |
| --- | --- | --- |
| `send(data)` | Codec interprets `data`; returns the event for archiving, possibly with a receipt, or `None` for an oversized single event. | Progress is produced; may synchronously trigger a checkpoint. |
| `finish(status=..., completion=..., error_code="")` | Codec constructs a terminal from `status`; store saves `completion`; `error_code` only enters the close summary. Returns `None`. | Computation finishes; commit before closure and notification. |
| `close_for_retry()` | Attempts a checkpoint and closes, without a final result or terminal event; repeated calls after closure return immediately. | The host prepares a retry or exits this run; it does not reschedule work itself. |

Default `JsonEventCodec.terminal` accepts only `SUCCESS` / `FAILED`. Counter uses `DONE` / `FAILED`; Demo defines `DONE` / `ERROR` / `INTERRUPTED`. These codec business statuses are separate from `Integrity`.

A returned event does not prove publication or persistence: degraded live writes may return the original event, and archiving may still be pending. Terminal encoding or commit failures retain pending/open state; the host identifies the cause and whether a commit occurred before retrying. `close_for_retry` still closes on configured archive failures, so it is not a durable-save acknowledgment.

### Completion and retry closure

| Call | Persistence and instance state |
| --- | --- |
| `finish` encoding or commit fails | Propagate; retain pending and open state, with no live terminal or close notification. |
| Call `finish` again on that instance | Construct a new terminal and try commit again; success closes it, and later calls raise the closed error. Computation is not rerun. |
| `close_for_retry` | Try only a checkpoint, not a final-result commit. Configured archive failures still close it, possibly retaining unsaved pending. Calls after closure are no-ops and do not retry persistence. |

After a final-commit timeout, the host checks durable state first. Another call constructs a new terminal, so storage needs execution/transaction idempotency rather than assuming an identical terminal object. Unconfigured `close_for_retry` errors, including default `StaleExecution`, propagate without closing; the host decides how to handle stale execution.

### Failures by stage

| Stage | Runtime / reader behavior |
| --- | --- |
| Invalid event construction/encoding or cursor | `InvalidEvent` / `InvalidCursor` and similar errors propagate; correct the input. |
| A write reenters before commit while the instance is open | Propagate `ReentrantStreamOperation`, not a recoverable backend failure; post-commit notifications follow the separate rule below. |
| Business publication raises `live_errors` | Record `DEGRADED`, continue archiving; later events may still publish. |
| Intermediate checkpoint / activity update raises `archive_errors` | Record `DEGRADED`; failed checkpoints retain pending, allowing recovery on later events. |
| Default `StaleExecution` or other unconfigured port errors | Propagate to the host; stale writers are not transient network failures. |
| Final `commit` fails | Always propagate; no close, live terminal, or observer notification. Storage owns transactions and retry idempotency. |
| Post-commit terminal publication / `after_close` fails | Catch `Exception` without changing the committed result. Terminal failure degrades memory integrity without writing it back to storage. |

### Notify a previous execution's stream

`announce_replacement(previous_publisher) -> bool` uses the new Runtime's `codec.reset("execution_replaced")`, validates it, then publishes to the previous execution's publisher. The host first commits execution rotation and fencing; both ends use matching codecs. A closed Runtime raises its closed error before publication.

Except for `ReentrantStreamOperation`, which propagates, only configured `live_errors` from publish are caught: log the new execution identity and error type, mark the new Runtime DEGRADED, and return False. Construction/encoding and other failures propagate. True means publish returned, not confirmed reader delivery. This method invokes no policy factory, consumes no event budget, changes no pending/archive data, closes nothing, and does not retry. Host code owns retry decisions, reader state checks, and transactions.

## 3. Configure capacity and batches

### Fixed policies and configuration per send

`policy=StreamPolicy(...)` retains fixed-policy behavior. Alternatively, pass `policy=current_policy`, a no-argument factory returning a `StreamPolicy`. The default remains a fixed object, and Runtime construction does not invoke factories.

Each `send` acquires the instance lock, checks that it is open, then invokes the factory once. Event size, live budget, pending, checkpoint, and activity decisions all use that policy. Configuration changes take effect on the next `send`, including checking existing pending against new thresholds:

```python
from examples.counter import make_counter
from ui_stream import StreamPolicy

limits = {"checkpoint_event_count": 2}


def current_policy():
    return StreamPolicy(**limits)


runtime, channel, store = make_counter(policy=current_policy)
runtime.send(4)
assert store.snapshot.latest is None
limits["checkpoint_event_count"] = 1
runtime.send(9)
assert store.snapshot.latest == 9
runtime.finish(status="DONE", completion=13)
```

A real host can read its own configuration in the factory, such as Django settings, without adding Django to the library. Factories should only compute or read configuration. Keep them lightweight and free of backend I/O; do not call `send` / `finish` / `close_for_retry` on the same Runtime from a factory. Factory exceptions propagate; a non-`StreamPolicy` result raises `TypeError`. Both occur before business-event processing, counters, or external writes, and are not treated as live/archive degradation.

`finish` / `close_for_retry` do not use policy thresholds and do not invoke the factory. A closed `send` raises `ClosedStream` first, without a factory call. `runtime.policy` remains read-only: fixed values are returned unchanged; each external property read invokes a factory again. Separate property reads are not a single dynamic snapshot.

Policy updates do not reset cumulative event/byte counts or restart already stopped live publication/archive buffering. Existing integrity does not recover. Updates change future threshold decisions, not execution state or background scheduling.

### Capacity and batch thresholds

These limits govern business progress; terminals use a separate commit/publication path. Bytes come from codec encoding, not raw `data` size.

| `StreamPolicy` field | Default | Threshold behavior |
| --- | --- | --- |
| `max_event_bytes` | 256 KiB | A single LIVE encoding **over** this size is rejected; `send` returns None and records TRUNCATED. |
| `max_events` | 10000 | The first 10000 business events passing single-size validation consume the budget; further events stop new archive buffering and record TRUNCATED, while live publication may continue. |
| `max_live_bytes` | 10 MiB | If another successful-publication LIVE byte total would exceed budget, permanently stop business publication in this instance and record DEGRADED. |
| `max_archive_bytes` | 10 MiB | Bounds the sum of pending ARCHIVE bytes. First try to archive existing pending; if the new event still cannot fit, discard it, stop future buffering, and record TRUNCATED. Excludes durable array/envelope overhead. |
| `checkpoint_event_count` / `checkpoint_bytes` | 20 / 256 KiB | Pending count or ARCHIVE bytes reaching the threshold trigger a synchronous checkpoint. |
| `activity_interval_seconds` | 60 seconds | Check on event arrival: checkpoint normally, or `touch_activity` after archiving stops; not a background heartbeat. |

The live byte budget counts only publications that return successfully. An uncertain write or publisher-internal retry can send bytes without a successful receipt, so this is not a hard limit on total network traffic.

`max_events` is cumulative for one execution and does not reset after checkpoints. With a budget of 2 and a checkpoint per event, sending 1–4 can leave the latest snapshot at 2 while live progress and the final result reach 4. Even a constant-space latest-value store is subject to this policy. Long tasks need an explicit budget or execution-rotation plan; there is no built-in unlimited-latest-value switch.

`StreamPolicy` requires non-negative built-in `int` values at construction. Other types, including bools, int subclasses such as IntEnum, floats, and strings, raise `TypeError`; negative values raise `ValueError`. Zero is not a universal disable switch:

| Zero-valued field | Meaning |
| --- | --- |
| `max_event_bytes` | Reject single events with a positive-length LIVE encoding. |
| `max_events` | Stop archive buffering from the first event; live publication can continue. |
| `max_live_bytes` | Stop positive-length business publication; archiving can continue. |
| `max_archive_bytes` | Positive-length ARCHIVE encodings cannot enter pending. |
| `checkpoint_event_count` / `checkpoint_bytes` | Try a checkpoint after each append to pending. |
| `activity_interval_seconds` | Check activity on each arriving event; do not disable activity refresh. |

Custom codecs may return empty bytes, so zero-byte budgets still accept zero-length encodings. Business budgets do not constrain terminals. The store separately limits the **entire durable snapshot**; pending capacity is not a database archive cap.

### Activity timestamps and watchdogs

A successful checkpoint refreshes Runtime's local activity baseline. After archiving stops, `touch_activity(binding=...)` is called only when another business event arrives and the interval is due. There is no background renewal without send; SSE heartbeats do not prove that production computation is active.

If a host watchdog uses activity timestamps, both checkpoint and touch_activity should update durable `last_active_at` after validating the current execution, using a consistent host clock. Runtime's monotonic value only controls this instance's call frequency. Long computations without progress need the host executor's own liveness mechanism. [Counter](../examples/counter.py) and the [SQLite Demo](../demos/sqlite/storage.py) have no watchdog, so touch only performs fencing, without an unused timestamp field.

## 4. Interpret integrity, receipts, and close summaries

Despite the name `archive_status`, live-channel failures also enter this state to signal possibly missing UI progress:

| `Integrity` | Meaning |
| --- | --- |
| `COMPLETE` | No known live/archive failure or discarded event; not a promise to retain complete history forever. |
| `DEGRADED` | An observed backend failure, write uncertainty, or a live-budget stop; data loss is not confirmed and the task may still succeed. |
| `TRUNCATED` | Progress was discarded due to event size, cumulative count, pending, or storage capacity. |

`merge_archive_status(current, incoming)` preserves the more severe state; later success does not erase earlier issues. Storage returns `Checkpoint(archive_status, capacity_exhausted)` only after handling the whole batch. Exactly filling capacity without dropping data may remain COMPLETE; the next discarded event raises TRUNCATED.

After closure, `after_close(summary)` runs once outside the lock. `Summary.status` is the finish business status or `RETRY`. `degraded` / `truncated` independently record observed conditions, may both be true, and include initial integrity. `event_count` / `event_bytes` accumulate after single-size validation and before publication: **they are not successful publication/archive counts** and exclude terminals. Summary contains no event body or Runtime reference. Post-commit terminal failure may be reflected only in memory and the summary.

## 5. Choose an event and read protocol

`ui_stream`, `types`, `exceptions`, `archive`, and `sse` work with the standard-library core. Default `codec` and `reader` require `[json]`; actual `RedisLiveStore` also needs `[redis]`. An optional profile is a ready-made set of events/adapters; the core does not require it.

Default `Event(event=None, data=...)` is business progress. `ControlEvent.STREAM_END` ends a stream; `STREAM_RESET` asks the host to reselect an execution. `stream_id` carries an ID actually generated by Redis; host-generated control hints normally leave it empty rather than inventing a resume position. Field assignment is frozen, but nested `data` is not deeply frozen and must not be mutated after acceptance. Default JSON encoding rejects NaN/Infinity.

### Redis tail to SSE

For writes, wrap `RedisLiveStore` in `BoundRedisPublisher(store, execution_key)` and pass it as `StreamRuntime.publisher`, with a matching `JsonEventCodec()`. Reads use the same store and execution key. The host creates/closes the Redis client and injects it into the store; see [redis.py](../src/ui_stream/redis.py) for adapter constructors.

The host authorizes the request, selects an execution/key, and supplies an existing store to this function. The HTTP layer sends/flushes its frames; the function does not create an HTTP response or query snapshots:

```python
from ui_stream.reader import iter_live, normalize_cursor
from ui_stream.sse import SSEBody


def sse_frames(store, key, last_event_id, state_check=None):
    cursor = normalize_cursor(last_event_id)
    events = iter_live(
        redis_store=store, redis_key=key, cursor=cursor, state_check=state_check
    )
    return SSEBody(events)
```

`normalize_cursor` turns empty/false values into `0-0`; otherwise it requires two ASCII decimal uint64 components, preserving valid leading zeroes. It checks neither event existence nor permissions. `iter_live` yields Event or None; the host encodes None as an SSE comment heartbeat. Heartbeats advance neither cursor nor business-idle time. The example uses `block_ms=15000` (up to 15 seconds per read) and `idle_timeout=300` (a 5-minute business-idle deadline). With the default absence of state hints, reaching that deadline without business events also ends iteration. Adjust both for long tasks as needed; see [reader.py](../src/ui_stream/reader.py).

Long connections can use `state_check` to recheck permission and execution validity. It takes no arguments, checks before the first live read, then defaults to 15-second intervals, and returns a control Event or None; permission exceptions propagate. Initial reset/state failures occur before any live read; this does not replace authorization before creating the HTTP response. A host end hint first drains published tail data without blocking. A real end retains its cursor; otherwise a raw empty read releases the synthetic end. A dirty-payload-only batch is not an empty tail. Reset hints close the old stream immediately. The initial check adds one callback invocation; whether it performs I/O depends on the host. Keep it lightweight to bound first-event latency.

Each `RedisLiveStore` append executes XADD and sliding TTL refresh within Lua, returning a new event with the real ID; no automatic resend/deduplication. Prefer `decode_responses=False` so bad payloads can be skipped individually while the scan cursor advances. If client pre-decoding fails before returning any ID, it becomes LiveUnavailable. Configured read errors end iteration **without synthesizing completion**; snapshot reload and reconnect policy belong to the host. In the default adapter `block_ms<=0` means nonblocking, not Redis `BLOCK 0`'s infinite wait.

### Synchronous SSEBody resource ownership

`SSEBody(events, *, encoder=...)` is a lazy synchronous `Iterator[bytes]` requiring an acquired Iterator. The default encodes Event/None; custom encoders handle every item, return bytes, and can map host exceptions. body.close forwards source close at most once, including before first next, normal exhaustion, and read/encoding failures. Direct close errors propagate; ordinary cleanup failures during an existing read/encoding failure log only the type and preserve that original error. StopIteration from the encoder or exhaustion cleanup becomes RuntimeError with its cause to prevent false EOF; explicit close preserves the original exception.

The HTTP layer closes the body on exit. It owns only the supplied iterator, not an outer Iterable, shared client, or producer. An unstarted generator's finally is not guaranteed; close cannot interrupt blocking next. Concurrent calls and async iterators are unsupported. See the [migration guide](migration.en.md) for a runnable custom encoder.

Implement blocking cancellation at the source or HTTP boundary: use finite backend-read timeouts, observe request cancellation at later read boundaries, and register `body.close` with response exit/close hooks. Do not concurrently close a generator executing next. Resources acquired in advance need an explicitly closeable iterator or outer context manager, not an unstarted generator's finally. Moving synchronous reads into an ASGI thread pool does not forcibly stop a running thread.

### Host-owned bounded JSON event lists

`encoded_archive_bytes(events, event_size=...)` measures a complete compact JSON array, including brackets and commas; default per-record sizing uses `model_dump(mode="json")`. `fit_archive_events(existing=..., incoming=..., max_bytes=..., event_size=...)` returns **(new list, accepted incoming count, whether incoming was discarded)**. It keeps existing data plus a fitting contiguous prefix, without modifying input lists or deduplicating.

If persisted records contain fields excluded from public serialization, `event_size` must include them. Its sizing must match actual compact JSON encoding to equal storage size. These helpers only measure and fit; queries, transactions, permissions, and archive replay stay in the host.

For retry deduplication, use `append_archive_events(existing, incoming, *, max_bytes, identity, event_size=None) -> ArchiveAppendResult`. It deduplicates by host-supplied Hashable IDs before calling fit. Only None means no identity; it neither deduplicates bodies nor generates IDs. Existing duplicates/oversized data stay unchanged; the first incoming event for an ID wins. Callback errors propagate.

The result's `events` is a new list; `appended_count`, `duplicate_count`, and capacity `dropped_count` sum to incoming length. `truncated` is dropped_count > 0. Deduplication precedes fitting; the first rejected candidate prevents later smaller candidates from being accepted. The host guarantees immutable identities/input data and saves the result in its own transaction. See the [migration guide](migration.en.md) for examples and boundaries.

## 6. Migrate embedded source

Pin the needed version/extra, remove embedded `ui_stream` to avoid shadowing the installed package, regenerate locks, and verify the actual import location. Keep business models, permissions, task exceptions, transactions, and read endpoints in the host. Repository examples/Demo are not business dependencies available inside the wheel.

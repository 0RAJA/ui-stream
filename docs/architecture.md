# Architecture and extension boundaries

```text
host task -> EventCodec -> StreamRuntime -> bound LivePublisher -> live readers
                              |
                              +-> ArchiveStore -> snapshot + terminal + result
                              |
                              +-> frozen after_close summary (after closure)
```

`ui_stream.runtime` and `ui_stream.types` use only the standard library. Core modules
do not import the default JSON/Pydantic or Redis profile. SQLite is a Demo adapter,
not a core mechanism. The host owns execution identity, concurrency between processes,
access checks, task lifecycle, result storage, and transaction/fencing decisions.

## Implement three ports

- `EventCodec[E]`: create business/terminal values and deterministically encode each
  for LIVE or ARCHIVE. No I/O or mutation. Your event need not inherit the default
  Pydantic Event and your terminal vocabulary need not be SUCCESS/FAILED.
- `LivePublisher[E]`: bound to an execution by the host; `publish(event) -> E` may
  return a new event with a transport receipt. It must preserve business content.
- `ArchiveStore[E]`: checkpoint, touch_activity, and commit. Runtime supplies tuple
  batches. Checkpoint updates the snapshot with fencing; commit atomically persists
  remaining tail, terminal and opaque completion, returning only after real commit.

Use [the counter](../src/ui_stream/example.py) for dataclass events, text frames and
an in-memory latest-value snapshot. Use [SQLite storage](../src/ui_stream/demo/storage.py)
for durable latest-value snapshots and transaction/fencing examples. Neither consumer
subclasses or patches Runtime. A production host should use its own storage adapter.

## Order and error handling

`send`: build/validate -> LIVE encoding/capacity -> live publish attempt -> ARCHIVE
encoding/capacity -> pending buffer -> optional checkpoint. A codec error can occur
after live publication if a receipt cannot be archived; callers must account for that.
Events and nested data must not be mutated after acceptance.

`finish`: construct/validate terminal -> encode both targets -> host atomic commit ->
clear pending and close -> best-effort terminal publish -> optional summary callback.
Commit failure leaves the Runtime open. Terminal publishing and callback failures after
successful commit cannot roll back the host result. Normal send infrastructure errors
are degraded only when configured; programming and fencing errors propagate.

`finish_retry` checkpoints and closes without terminal or final result; it is a host
retry boundary, not an automatic task runner. Activity touching is send-driven, not a
background heartbeat. Summary callbacks cannot participate in the transaction.

Default `codec`, `redis`, `reader`, `sse`, and JSON prefix helpers in `archive` form a
specific optional profile. `reader` is Redis-only; replacing the channel does not
magically supply a generic reader. Applications decide snapshot representation and
reader recovery. The Runtime makes no Redis key/TTL/read assumptions.

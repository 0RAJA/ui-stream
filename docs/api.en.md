# API contracts

[简体中文](api.md) · [Documentation](README.en.md)

Pin the exact alpha version `0.1.0a1`; run your adapter contracts before upgrading. Names beginning with an underscore are private.
[types.py](../src/ui_stream/types.py) and [runtime.py](../src/ui_stream/runtime.py) define authoritative signatures.

| Entry | Methods / types |
| --- | --- |
| `StreamRuntime` | `send` / `finish` / `finish_retry` |
| `EventCodec` | `business` / `terminal` / `encode` |
| `LivePublisher` | `publish` |
| `ArchiveStore` | `checkpoint` / `touch_activity` / `commit` |
| `after_close` | `Summary` |

Public modules: `ui_stream`, `ui_stream.types`, `ui_stream.exceptions`. Optional profiles live in `codec`, `redis`, `reader`, `sse`, `archive`.
Default `JsonEventCodec` uses SUCCESS/FAILED; custom codecs may use unrelated events and terminal vocabularies.

| `StreamPolicy` | Default |
| --- | --- |
| `max_event_bytes` | 256 KiB |
| `max_events` | 10000 |
| `max_live_bytes` / `max_archive_bytes` | 10 MiB each |
| `activity_interval_seconds` | 60 |
| `checkpoint_event_count` / `checkpoint_bytes` | 20 / 256 KiB |

An oversized single event makes `send` return None. The event cap stops new archive buffering; the live-byte cap stops new business publication, while terminal publication is still attempted.
Stores enforce their own durable snapshot cap. Activity updates are driven by `send`, not a timer.
`finish_retry` checkpoints and closes without constructing a terminal event or committing a final result.

For host adoption, pin the extra/version, remove embedded `ui_stream` to prevent shadowing, regenerate dependency locks, and verify the import location. Keep models, permissions, transactions, and task exceptions in the host.

## Finite archive budgets and truthful integrity

`max_events` is the cumulative archive-event budget for one execution, not the pending batch size. With a budget of 2 and a checkpoint per event, sending 1–4 can leave the latest snapshot at 2 while live progress and the final business result reach 4, with TRUNCATED integrity. This policy still applies when the store retains only a constant-size latest value. Budget finite tasks explicitly; long tasks need explicit execution rotation or a policy/implementation suitable for an unlimited latest value. The defaults are not an unlimited snapshot service.

`Checkpoint(capacity_exhausted=True)` stops future archive buffering. A batch that exactly fills storage without dropping anything may remain COMPLETE; the next actually discarded event raises TRUNCATED. `Summary` flags also reflect integrity supplied at construction.

## Optional Redis reader

`normalize_cursor` accepts two ASCII uint64 Redis stream-ID components. The reader does not provide authorization, initial snapshots, or replay on reconnect.
With `decode_responses=False`, the adapter can skip dirty payloads individually and advance real entry IDs. Normal string responses are also supported. A `decode_responses=True` client can fail on invalid UTF-8 before returning any entry, however; this becomes `LiveUnavailable` and ends the current reader so the host can reload a snapshot. No unknown cursor is guessed, and skipping that entry is not promised.

## Host state checks and archive sizing

`iter_live(..., state_check=None, state_poll_interval=15)` accepts a no-argument host check returning a control event or `None`. Durable end hints drain already published tail data; original end events retain their cursor, and a synthetic end follows only a raw empty batch. Reset closes immediately and host permission exceptions propagate. Checks are disabled by default and import no host models.

`encoded_archive_bytes(events, event_size=None)` and `fit_archive_events(..., event_size=None)` share a per-event size function, defaulting to model_dump sizing. Hosts with hidden persisted fields supply their actual record sizing; prefix fitting stays in one implementation.

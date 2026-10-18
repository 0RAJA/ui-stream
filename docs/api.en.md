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

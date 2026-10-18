# Public API and compatibility

The initial alpha API may change between alpha releases; consumers should pin an
exact version, run their adapter contracts, and review the changelog before upgrading.
No stable 1.x compatibility guarantee is made for this candidate.

- `ui_stream`: StreamRuntime, StreamPolicy, Integrity.
- `ui_stream.types`: Binding, ExecutionBinding, EventCodec, LivePublisher, ArchiveStore,
  EncodingTarget, Checkpoint, Summary, AfterClose and ReadBatch.
- `ui_stream.exceptions`: InvalidEvent, InvalidCursor, ClosedStream, StaleExecution,
  LiveUnavailable and ArchiveUnavailable.
- Optional `ui_stream.codec`: Event, ControlEvent, JsonEventCodec, serialize_data,
  serialize_event. Default terminal statuses are SUCCESS and FAILED.
- Optional `ui_stream.redis`: RedisLiveStore and BoundRedisPublisher.
- Optional `ui_stream.reader`: normalize_cursor and iter_live.
- Optional `ui_stream.sse`: encode_sse_event.
- Optional `ui_stream.archive`: merge_archive_status and fit_archive_events.

The type Protocols are the authoritative signatures. See
[types.py](../src/ui_stream/types.py) and [runtime.py](../src/ui_stream/runtime.py).
Names beginning `_` are private and outside this compatibility contract. Demo HTTP
routes/schema are examples, not a production supported service API.

## Defaults and limits

StreamPolicy defaults: max_event_bytes 256 KiB; max_events 10,000;
max_live_bytes 10 MiB; max_archive_bytes 10 MiB; activity interval 60s;
checkpoint_event_count 20 and checkpoint_bytes 256 KiB.

Oversized single events return None. Event count stops new archive buffering; live
business tail may continue. The live byte cap stops new business publishing; terminal
publication is still attempted. Stores impose their own persistent snapshot cap.
Integrity advances COMPLETE -> DEGRADED -> TRUNCATED and does not improve later.

## Host adoption

Use a real version dependency such as `ui-stream[redis]==0.1.0a1` only after that
name/version is approved and available from the selected source. Remove any embedded
`ui_stream` package so it cannot shadow the dependency. Keep application-specific
adapters, models, permissions, task errors and telemetry in the application. Re-lock
and export application dependencies with that application's package manager.

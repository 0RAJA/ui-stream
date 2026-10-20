# Choosing a solution

[简体中文](comparison.md) · [Documentation](README.en.md)

This compares responsibilities and integration costs using official documentation checked on 2026-10-18 and ui-stream `0.1.0a1` contracts. Measurements are in the [performance baseline](performance.en.md).

## When to choose ui-stream

When the host already schedules background work and owns business-result transactions, while the UI needs a latest snapshot and best-effort live progress, ui-stream reuses buffering, checkpoints, integrity tracking, and completion ordering. The core is synchronous with no third-party dependencies; Pydantic/Redis are optional profiles.

`finish()` calls the host's `ArchiveStore.commit()` first. Success permits runtime closure, live terminal publication attempts, and `after_close`. Commit errors propagate and leave the runtime open. The host must atomically commit the tail, terminal, and business result with fencing; separate databases and message publication do not become one transaction automatically.

Accept these integration costs and boundaries:

- The host implements ports, authorization, reads, transactions, and task lifecycle. Synchronous calls may block; ASGI needs threads or an adapter.
- The supplied reader uses Redis cursors; the default SSE encoder uses default Event objects and Redis-format IDs. Other protocols can provide their own implementation, as the Demo does. Adapters manage HTTP queues, client backlog, and storage retention.
- `max_events` is a cumulative archive budget. Snapshots may stop while live progress/results continue; indefinite latest-value services need a different policy. See [API](api.en.md).

If existing SSE, event storage, or agent streams meet your needs, this coordination layer may be unnecessary. See [architecture](architecture.en.md) for ports and completion order.

## Choose by requirement

| Requirement | Prefer | The application still supplies |
| --- | --- | --- |
| Bounded snapshot/progress coordination for existing jobs and transactions | **ui-stream**: three synchronous ports, checkpoints, integrity state, notification after commit | Atomic transactions, execution fencing, authorization, HTTP, reads, and retries; no complete history, lossless reconnects, or computation recovery |
| Send an existing iterator as an HTTP response | **Starlette / FastAPI [`StreamingResponse`](https://github.com/Kludex/starlette/blob/main/docs/responses.md#streamingresponse)** | SSE framing, storage, reconnect behavior, and business-result commits |
| ASGI SSE connection handling in Starlette/FastAPI | **[sse-starlette](https://github.com/sysid/sse-starlette#server-sent-events-for-starlette-and-fastapi)**: encoding, pings, disconnect detection, `send_timeout` | Event sources, history, cursor recovery, and result transactions; SSE `id` does not create an archive |
| Typed SSE directly from FastAPI endpoints | **[FastAPI built-in SSE](https://fastapi.tiangolo.com/tutorial/server-sent-events/)**: `EventSourceResponse` / `ServerSentEvent`, idle pings, and default headers since `0.135.0` | Continuation after `Last-Event-ID`, retention, and transactions; sufficient when only encoding/heartbeats are needed |
| Recover missed Django events within retention | **[Django EventStream](https://github.com/fanout/django-eventstream#event-storage)**: authorization hooks, optional write-before-publish storage, 24-hour default retention | Explicitly enable storage; configure Redis/GRIP for multiprocess publication; handle retention resets, result transactions, and execution lifecycle |
| Resume computation, approvals, or agent state | **LangGraph**: [thread checkpoints](https://docs.langchain.com/oss/python/langgraph/checkpointers), interrupt/resume, and [streaming](https://docs.langchain.com/oss/python/langgraph/streaming) | Choose [persistent checkpointers and durability](https://docs.langchain.com/oss/python/langgraph/persistence); memory does not survive restarts; [replayed effects require idempotency](https://docs.langchain.com/oss/python/langgraph/interrupts#side-effects-called-before-interrupt-must-be-idempotent); integrate external-result/UI transactions |

These layers can compose: ui-stream could coordinate progress/commits while ASGI SSE transports events; LangGraph could recover execution while the application maps UI events. The repository does not supply or verify these combinations.

## Three meanings of recovery

- **Restore the current UI:** read the latest snapshot/result, then attach the live tail; intermediate progress may be missing. This is ui-stream's target, with host-owned reads and handoff.
- **Recover events:** retain events and implement cursor recovery. Django EventStream's storage mode targets this requirement more directly.
- **Resume computation:** persist execution state and arrange checkpoint-based scheduling through LangGraph or another execution framework. ui-stream's `finish_retry()` only checkpoints and closes; it does not rerun tasks.

None implies end-to-end exactly-once delivery. ui-stream does not deduplicate events; SSE IDs, process-local locks, and host fencing have different scopes.

## Version sources

| Solution | Official version source |
| --- | --- |
| sse-starlette | [v3.5.0 release](https://github.com/sysid/sse-starlette/releases/tag/v3.5.0) |
| Starlette | [1.8.0 release](https://github.com/Kludex/starlette/releases/tag/1.8.0); response behavior follows official main documentation |
| FastAPI | [master `__version__=0.143.1`](https://raw.githubusercontent.com/fastapi/fastapi/master/fastapi/__init__.py); [0.135.0 release notes](https://fastapi.tiangolo.com/release-notes/#01350) record SSE introduction |
| Django EventStream | [master metadata 5.3.4](https://raw.githubusercontent.com/fanout/django-eventstream/master/pyproject.toml) |
| LangGraph | [1.2.16 release](https://github.com/langchain-ai/langgraph/releases/tag/1.2.16) |

These identify documentation sources, without deployment tests or framework network comparisons. The performance baseline's sse-starlette **3.2.0** participated only in encoding measurements.

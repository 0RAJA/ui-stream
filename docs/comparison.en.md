# Choosing ui-stream and related solutions

[简体中文](comparison.md) · [Documentation](README.en.md)

Checked on 2026-10-18. ui-stream follows this repository's `0.1.0a1` candidate contracts; other projects use the official sources below. This compares responsibilities and integration work, not performance rankings. No additional dependencies were installed and no other framework's network benchmark was run.

## Why consider ui-stream

Consider ui-stream when your host already schedules background work and owns the business-result transaction, while the UI needs a current snapshot and a best-effort live progress tail. Three ports separate event construction/encoding, live publication and archive commits. The synchronous runtime reuses buffering, checkpoints, integrity tracking and completion ordering. Its core has no third-party runtime dependencies; Pydantic and Redis are optional profiles.

The specific contract is that `finish()` calls the host's `ArchiveStore.commit()` first. Only a successful return allows the runtime to close, attempt live terminal publication and call `after_close`. Commit errors propagate and leave the runtime open. The host must actually commit the tail, terminal and business result atomically, with appropriate fencing. The library cannot turn separate databases or message publication into one transaction.

The tradeoff is integration work: the host implements the ports, authorization, reads, transactions and task lifecycle. Synchronous port calls may block; ASGI hosts must arrange threads or another adapter. The supplied reader/SSE helpers target the Redis profile, rather than arbitrary custom channels. `StreamPolicy` bounds runtime buffering and publication/archive policies; adapters still manage HTTP queues, client backlog and retention. See [architecture](architecture.en.md), [API](api.en.md) and the [source contracts](../src/ui_stream/types.py).


`max_events` is a cumulative archive budget: after it is reached, snapshots may stop while live progress and results continue. This suits budgeted finite executions; an indefinitely updated latest-value service needs an explicit policy choice. See [API boundaries](api.en.md).

## Choose by requirement

| Solution | What it provides | Prefer it when | What the application still supplies |
| --- | --- | --- | --- |
| **ui-stream** | Three synchronous ports; bounded pending buffers, checkpoints and integrity state; completion notifications attempted after the host commits | Existing jobs and transactions need UI snapshots/progress and a reusable commit/degradation contract | Real transactions, execution identity/fencing, authorization, HTTP, reads and retries. Snapshot plus live tail does not promise complete history, lossless reconnects or computation recovery |
| **Starlette / FastAPI `StreamingResponse`** | An HTTP response body streamed from a sync/async iterator. [Official Starlette docs](https://github.com/Kludex/starlette/blob/main/docs/responses.md#streamingresponse) | You only need to send an existing iterator, or already have the necessary state, storage and reconnect rules | This response class does not define SSE events, business-result commits, storage or history recovery. Compose those separately; consider an SSE response below when appropriate |
| **sse-starlette** | `EventSourceResponse`, SSE encoding, pings, disconnect detection and `send_timeout`. [Official usage](https://github.com/sysid/sse-starlette#server-sent-events-for-starlette-and-fastapi) | A Starlette/FastAPI application needs ASGI SSE connection handling | Event sources, persisted cursor history and result transactions. Setting SSE `id` alone creates no event archive. It could transport ui-stream events through an application adapter |
| **FastAPI built-in SSE** | Since `0.135.0`, `fastapi.sse.EventSourceResponse` / `ServerSentEvent`, typed data, idle pings and default response headers. [Official SSE tutorial](https://fastapi.tiangolo.com/tutorial/server-sent-events/) | Your FastAPI version supports it and endpoints should stream models or SSE events directly | The documented `Last-Event-ID` example leaves selecting the continuation point to application code. Retention, business transactions and recovery policy remain application concerns. SSE encoding and heartbeats alone do not require ui-stream |
| **Django EventStream** | Django SSE views and channel-authorization hooks; optional event storage writes before publication, retains events for 24 hours by default and repairs interrupted streams. [Official storage/client docs](https://github.com/fanout/django-eventstream#event-storage) | A Django application needs missing intermediate events recovered within a retention window | Storage is disabled initially; multiprocess publication requires Redis or GRIP deployment configuration. Clients handle resets after falling behind retention. Business-result transactions and execution lifecycle still need design |
| **LangGraph** | Graph/agent execution, thread checkpoints, fault recovery, interrupt/resume, and state/token/custom output streams. [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers), [streaming](https://docs.langchain.com/oss/python/langgraph/streaming) | The thing to resume is computation, approval or agent state | Select a persistent checkpointer and durability mode. An in-memory saver does not survive restarts. [Persistence boundary](https://docs.langchain.com/oss/python/langgraph/persistence). Account for node re-execution and idempotent effects before interrupts. [Official constraint](https://docs.langchain.com/oss/python/langgraph/interrupts#side-effects-called-before-interrupt-must-be-idempotent). Explicitly integrate external business-result and UI transactions |

These tools can compose. A host task could use ui-stream for progress and final commits, with an ASGI SSE response transporting events. LangGraph could recover execution while application code maps relevant updates to UI events. These are possible responsibility boundaries, not integrations supplied or verified by this repository. If your existing SSE, event storage or agent stream meets the requirements, ui-stream may add little value.

## Three different meanings of recovery

- **Restore the current UI:** read the latest snapshot and business result, then attach the live tail. This suits progress displays that tolerate missing intermediate updates. It is ui-stream's intended use; the host implements the reads and handoff.
- **Recover missing events:** retain events and implement a cursor recovery protocol. Django EventStream's storage mode targets this requirement more directly. ui-stream's default snapshot/live model gives no lossless-history guarantee.
- **Resume unfinished computation:** persist execution state and arrange execution from checkpoints. LangGraph and other execution frameworks address this layer. ui-stream's `finish_retry()` only checkpoints and closes the current runtime; it does not rerun the task.

None of these terms should be treated as an end-to-end exactly-once promise. ui-stream performs no automatic event deduplication; SSE IDs, local locks and host fencing have distinct scopes.

## Official versions reviewed

| Solution | Reviewed source |
| --- | --- |
| sse-starlette | [v3.5.0 release](https://github.com/sysid/sse-starlette/releases/tag/v3.5.0), [main VERSION=3.5.0](https://raw.githubusercontent.com/sysid/sse-starlette/main/VERSION) |
| Starlette | [1.8.0 release](https://github.com/Kludex/starlette/releases/tag/1.8.0); `StreamingResponse` checked against official main documentation |
| FastAPI | [master `__version__=0.143.1`](https://raw.githubusercontent.com/fastapi/fastapi/master/fastapi/__init__.py); [0.135.0 release notes](https://fastapi.tiangolo.com/release-notes/#01350) record built-in SSE's introduction |
| Django EventStream | [master `pyproject.toml` version=5.3.4](https://raw.githubusercontent.com/fanout/django-eventstream/master/pyproject.toml); source metadata was reviewed, without a deployment test |
| LangGraph | [1.2.16 release](https://github.com/langchain-ai/langgraph/releases/tag/1.2.16), [main metadata](https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/langgraph/pyproject.toml). The old `durable-execution` URL redirected to `persistence`; current checkpoint details come from the checkpointers guide |

These identify reviewed sources, not versions exercised by the performance report. Its installed `sse-starlette 3.2.0` was used only for encoding measurements. Those results do not establish ASGI connection capacity, recovery behavior or an overall performance ranking of these solutions.

# SQLite browser Demo

[简体中文](README.md) · [Project home](https://github.com/0RAJA/ui-stream)

This example connects a 12-step task to a browser: live progress, SQLite snapshots/results, refresh recovery, live failure, and commit retries. It uses standard-library HTTP/SQLite, a framework-free page, and synthetic data. macOS/Linux `fcntl` is required; Django, Redis, Node/npm, and external services are unnecessary.

## Run and observe

After installing the core, run from the repository root. Demo is excluded from wheel/sdist. Use a dedicated database outside the repository, never a business database:

```sh
python -m demos.sqlite --database /tmp/ui-stream-demo.sqlite3 --port 8765 --interval 0.5
```

Open `http://127.0.0.1:8765`, select a scenario, and create a job. Defaults are one step per 0.5 seconds and a snapshot every 3 steps. Completion commits the latest snapshot and result together, then the UI shows DONE. Stop with Ctrl-C. A second process cannot share the database; binding is loopback-only, without authentication.

Demo defines its own `progress` / `finished` events, integer cursors, and `DONE` / `ERROR` / `INTERRUPTED` statuses. It demonstrates the core ports without the optional JSON/Pydantic modules, Redis reader, or their cursor protocol.

| Scenario | Expected behavior and recovery owner |
| --- | --- |
| Normal | 12 steps; host storage commits the final snapshot/result before completion notification. |
| Business error | End at step 6 with saved error result and ERROR status: a business failure. |
| Live failure | Steps 4–7 unavailable live; computation/SQLite continue, integrity DEGRADED, business may still reach DONE. |
| Checkpoint failure | Runtime retains pending after the first failure; later events trigger recovery, keeping DEGRADED. |
| Commit failure | First failure leaves Runtime open without finished; the Demo host retries final commit on the same Runtime after about 1 second, without recomputing the task. |

## Why refresh and recovery work this way

Refresh first reads SQLite's latest snapshot/result, then follows the bounded memory tail. Intermediate frames may be missed, but current UI state can be restored. Reader disconnects do not cancel work; completed jobs show saved results. Service restart marks leftover RUNNING jobs INTERRUPTED, retains committed snapshots, and does not resume computation.

Button state and server-side `request_token` prevent duplicate jobs for one creation request; refresh confirms pending tokens first. This is Demo host creation idempotency, not a core exactly-once event guarantee. If the producer closes before durable completion is confirmed, the client stops automatic SSE resubscription while one-second snapshot queries and manual recovery remain available.

## HTTP API and implementation entry points

| HTTP | Purpose |
| --- | --- |
| `POST /api/jobs` | JSON `request_token` UUID and `scenario`; create or recover the job for that request. |
| `GET /api/jobs/<id>` | Read SQLite status, latest snapshot, and final result. |
| `GET /api/jobs/<id>/events?after=n` | Native SSE; state then close on terminal, reset on gaps, sync prompts snapshot reload when completion notice is missing. |

Start with [service.py](service.py) for assembly/lifecycle, then [storage.py](storage.py) for transactions/ownership checks. [channel.py](channel.py) and [app.py](app.py) supply memory tail/HTTP; [demo.js](static/demo.js) implements browser recovery.

At most 4 active jobs and 32 channels. This is a local single-process example without full-history replay, lossless reconnects, cancellation, or production deployment guarantees. See [Architecture](../../docs/architecture.en.md) for core responsibilities.

## Tests

```sh
PYTHONPATH=src python -m unittest tests.test_demo tests.test_contract tests.test_demo_client -v
```

Contracts use isolated subprocesses, temporary SQLite, and real localhost HTTP. Existing Node runs controlled state tests of the actual `demo.js`; browser interaction needs separate verification.

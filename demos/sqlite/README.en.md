# SQLite browser Demo

[简体中文](README.md) · [Project home](https://github.com/0RAJA/ui-stream)

Standard-library HTTP/SQLite and a framework-free page, using synthetic data only. Requires macOS/Linux `fcntl`; no Django, Redis, Node/npm, or external service.

After installing the core, run from the repository root (the Demo is excluded from wheel/sdist):

```sh
python -m demos.sqlite --database ./demo.sqlite3 --port 8765 --interval 0.5
```

Open `http://127.0.0.1:8765`; stop with Ctrl-C. A dedicated `--database` is required; a second process cannot share it. Binding is loopback-only, without authentication.

Refresh/reconnect reads the latest SQLite snapshot, then follows the bounded memory tail. Reader disconnects do not cancel tasks; completed jobs show saved results. Restart marks leftover RUNNING jobs INTERRUPTED, retaining committed snapshots without resuming computation.

## Scenarios

| Scenario | Observation |
| --- | --- |
| Normal | 12 steps, checkpoint every 3; final snapshot and result commit atomically |
| Business error | ERROR at step 6, with a saved error result |
| Live failure | Steps 4–7 unavailable; computation/SQLite continue, DEGRADED, may still reach DONE |
| Checkpoint failure | First attempt retains pending; later recovery, DEGRADED |
| Commit failure | First attempt stays open without finished; retry the same Runtime after about 1 second |

Button state and server-side `request_token` idempotency protect repeated creation; refresh confirms pending creation tokens first. If the producer closes without a confirmed durable terminal state, automatic SSE resubscription stops while one-second snapshot polling and manual recovery remain available.

## HTTP API and boundaries

| HTTP | Purpose |
| --- | --- |
| `POST /api/jobs` | JSON `request_token` UUID + `scenario` |
| `GET /api/jobs/<id>` | SQLite state/snapshot/result |
| `GET /api/jobs/<id>/events?after=n` | Native SSE; state then close on terminal, reset on gaps, sync for missed notices |

At most 4 active jobs and 32 channels. The Demo does not guarantee complete replay, lossless reconnects, exactly-once, cancellation, cross-process live service, or production deployment.

## Tests

```sh
PYTHONPATH=src python -m unittest tests.test_demo tests.test_contract tests.test_demo_client -v
```

Contracts use isolated subprocesses, temporary SQLite, and real localhost HTTP. Existing Node runs controlled state tests of the actual `demo.js`; browser interaction needs separate verification.

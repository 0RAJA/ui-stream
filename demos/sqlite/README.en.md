# SQLite browser Demo

[简体中文](README.md) · [Project home](https://github.com/0RAJA/ui-stream)

Install the core wheel, then run this Demo from the repository root. The Demo is excluded from wheel/sdist.

Standard-library HTTP/SQLite and a framework-free page with synthetic data. Requires macOS/Linux `fcntl`; no Django, Redis, Node/npm, or external service.

```sh
python -m demos.sqlite --database ./demo.sqlite3 --port 8765 --interval 0.5
```

Open `http://127.0.0.1:8765`; stop with Ctrl-C. A dedicated `--database` is required; a second process cannot share it. Binding is loopback-only without reverse DNS.
Refresh/reconnect reads the latest SQLite snapshot then follows the bounded memory tail. Reader disconnect does not cancel computation. Completed jobs show saved results without recomputation.

| Scenario | Observation |
| --- | --- |
| Normal | 12 steps, checkpoint every3; final snapshot and result commit atomically |
| Business error | ERROR at step6 with a saved error result |
| Live failure | Steps4–7 unavailable; computation/SQLite continue, DEGRADED, may still DONE |
| Checkpoint failure | First attempt retains pending; later recovery, DEGRADED |
| Commit failure | First attempt stays open without finished; retry same Runtime after about1s |

Synchronous button state plus server request_token idempotency protect repeated creation; refresh confirms pending creation tokens first.
If the producer closes without confirmed durable terminal state, stop automatic SSE resubscription and keep one-second snapshot polling/manual recovery.
Restart marks leftover RUNNING jobs INTERRUPTED, retaining committed snapshots without resuming computation.

| HTTP | Purpose |
| --- | --- |
| `POST /api/jobs` | JSON `request_token` UUID + `scenario` |
| `GET /api/jobs/<id>` | SQLite state/snapshot/result |
| `GET /api/jobs/<id>/events?after=n` | Native SSE; state then close on terminal, reset on gaps, sync for missed notices |

At most4 active jobs and32 channels. No complete replay, lossless reconnects, exactly-once, cancellation, cross-process live service, or production deployment guarantee.

```sh
PYTHONPATH=src python -m unittest tests.test_demo tests.test_contract tests.test_demo_client -v
```

Demo contracts run under -I -S with temporary SQLite and real localhost HTTP. Existing Node runs controlled state tests of the actual demo.js; these do not replace real browser interaction.

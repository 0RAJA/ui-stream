# ui-stream

[简体中文](README.md) · [Documentation](docs/README.en.md) · [CI](https://github.com/0RAJA/ui-stream/actions/workflows/ci.yml)

**Send task progress to a UI, checkpoint snapshots, and commit final results in the host's own transaction.**
The synchronous core has no third-party dependencies. Three ports connect domain events, a live channel, and durable storage.

## Quick start

Use Python ≥3.11 in a virtual environment, from a source checkout:

```sh
python -m pip install --no-deps .
python -m examples.counter
python -m demos.sqlite --database ./demo.sqlite3 --port 8765
```

Open `http://127.0.0.1:8765`. The Demo uses standard-library HTTP/SQLite and synthetic data on macOS/Linux.
Counter and Demo live in repository `examples/` and `demos/`; run them from the repository root after installing the core. The default wheel contains only runtime code.
After its PyPI release, install `ui-stream==0.1.0a1`; the import name is `ui_stream`.

## Boundaries

| Suitable for | Host responsibilities / non-guarantees |
| --- | --- |
| Task progress, latest snapshots, best-effort live tail | Permissions, cross-process fencing, retries, result transactions |
| Bounded buffering, checkpoints, terminal events after commit | Complete replay, lossless reconnects, exactly-once delivery |
| Synchronous Python hosts, custom events and storage | Automatic computation recovery, reader-driven cancellation, a production Demo service |

## Extension entry points

Implement `EventCodec`, `LivePublisher`, and `ArchiveStore`; no inheritance or patching of `StreamRuntime` is needed.
The in-memory Counter shows custom events and results. The SQLite Demo shows durable snapshots, SSE, and failure recovery.

```python
from ui_stream import StreamPolicy
from examples.counter import make_counter

runtime, channel, store = make_counter(policy=StreamPolicy(checkpoint_event_count=1))
runtime.send(4)
runtime.send(9)
runtime.finish(status="DONE", completion=13)
assert store.snapshot.latest == 9 and store.snapshot.result == 13
```

| Profile | Dependencies |
| --- | --- |
| Core wheel | Standard library |
| Repository Counter / SQLite Demo | Standard library + installed core |
| `[json]` | Pydantic ≥2.10,<3 |
| `[redis]` | Pydantic ≥2.10,<3, redis-py ≥3.5.3 |

![Component boundaries: a task calls Runtime; the host implements three ports; the UI reads live tail and snapshots](docs/diagrams/architecture.en.png)

[Architecture and completion order](docs/architecture.en.md) · [API contracts](docs/api.en.md) ·
[Demo](demos/sqlite/README.en.md) · [Performance and comparisons](docs/performance.en.md) ·
[Contributing](CONTRIBUTING.en.md) · [Security](SECURITY.en.md) · [Releasing](docs/releasing.en.md)

The source CI matrix covers Python 3.11–3.14 on Linux/macOS; wheel, sdist, and README validation run on Linux.
Maintainer: `raja <rajawwang@foxmail.com>` (GitHub [0RAJA](https://github.com/0RAJA)).
[MIT and provenance](docs/license.en.md); upstream copyright notices are retained.

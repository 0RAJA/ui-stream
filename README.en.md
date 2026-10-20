# ui-stream

[简体中文](README.md) · [Documentation](docs/README.en.md) · [CI](https://github.com/0RAJA/ui-stream/actions/workflows/ci.yml)

**Send background-task progress to a UI, save progress snapshots in batches, and commit the final result before announcing completion.**

ui-stream fits Python applications that already own background tasks and business storage. It coordinates live publication, archive buffering, and completion order. The application using it (the “host”) supplies scheduling, permissions, and real storage transactions. The synchronous core has no third-party dependencies.

## Quick start

Use Python ≥3.11 and an activated virtual environment. To install only the library, pin the alpha version; its import name is `ui_stream`:

```sh
python -m pip install ui-stream==0.1.0a2
```

To run examples, obtain this repository and run the following from its **root directory**. Counter and the Demo are not included in wheel/sdist, so installing the PyPI package alone does not provide these commands:

```sh
python -m pip install --no-deps .
python -m examples.counter
python -m demos.sqlite --database /tmp/ui-stream-demo.sqlite3 --port 8765
```

Counter prints live text frames and its final memory snapshot. The SQLite Demo shows progress, failures, and recovery at `http://127.0.0.1:8765`; stop with Ctrl-C. It uses synthetic data and a dedicated SQLite database and requires macOS/Linux.

This complete Counter call sends 4 and 9 as successive progress values and 13 as the final result computed by the application. Latest progress and the final result are distinct data:

```python
from ui_stream import StreamPolicy
from examples.counter import make_counter

runtime, channel, store = make_counter(policy=StreamPolicy(checkpoint_event_count=1))
runtime.send(4)
runtime.send(9)
runtime.finish(status="DONE", completion=13)
assert store.snapshot.latest == 9 and store.snapshot.result == 13
```

Counter demonstrates the interfaces in memory; data disappears when the process exits. Its custom terminal status is `DONE`. The default JSON codec uses `SUCCESS` / `FAILED`; applications may choose their own events and statuses.

## Connect your application

After creating one execution, assemble `StreamRuntime` with three implementation objects: `EventCodec` turns business values into events, `LivePublisher` publishes live events, and `ArchiveStore` saves progress and the final result. Runtime inheritance is unnecessary. Start with the complete [Counter](examples/counter.py), then read [Architecture and integration](docs/architecture.en.md) and the [API](docs/api.en.md).

![Task calls Runtime; the host supplies three ports; the UI reads live events and snapshots](docs/diagrams/architecture.en.png)

| What it provides | What the application supplies |
| --- | --- |
| Bounded buffering, batch archiving (checkpoint), and notification after result commit | Scheduling, user permissions, transactions, execution ownership checks, and retries |
| Live progress plus latest snapshots for UI recovery | HTTP/SSE responses, snapshot queries, and reconnect read/handoff logic |
| Custom events, live channels, and snapshot stores | A history-retention policy; the core does not guarantee complete history, lossless reconnects, or exactly-once delivery |

| Installation | Purpose and dependencies |
| --- | --- |
| Core wheel | Standard library; Runtime, ports, and archive helpers |
| Repository Counter / SQLite Demo | Standard library + installed core |
| `[json]` | Default JSON events, Redis-cursor reader protocol, and SSE frames; Pydantic ≥2.10,<3 |
| `[redis]` | Adds the Redis live-store adapter; Pydantic ≥2.10,<3 and redis-py ≥3.5.3 |

[Demo](demos/sqlite/README.en.md) · [Performance charts](docs/performance.en.md) · [Choosing a solution](docs/comparison.en.md) · [Contributing](CONTRIBUTING.en.md) · [Security](SECURITY.en.md) · [Releasing](docs/releasing.en.md)

Source CI covers Python 3.11–3.14 on Linux/macOS; distribution and README checks run on Linux.
Maintainer: `raja <rajawwang@foxmail.com>` (GitHub [0RAJA](https://github.com/0RAJA)).
[MIT and attribution](docs/license.en.md); original copyright statements remain intact.

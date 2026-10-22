# ui-stream

[![CI](https://github.com/0RAJA/ui-stream/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/0RAJA/ui-stream/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ui-stream?include_prereleases=true)](https://pypi.org/project/ui-stream/)
[![License: MIT](https://img.shields.io/github/license/0RAJA/ui-stream)](https://github.com/0RAJA/ui-stream/blob/main/LICENSE)

[简体中文](README.md) · [Documentation](docs/README.en.md) · [CI](https://github.com/0RAJA/ui-stream/actions/workflows/ci.yml)

**Send background-task progress to a UI, save progress snapshots in batches, and commit the final result before announcing completion.**

ui-stream fits Python applications that already own background tasks and business storage. It coordinates live publication, archive buffering, and completion order. The application using it (the “host”) supplies scheduling, permissions, and real storage transactions. The synchronous core has no third-party dependencies.

## Installation

Use Python ≥3.11 and an activated virtual environment. Install the core package; its import name is `ui_stream`:

```sh
python -m pip install --pre ui-stream
```

## Minimal example

Counter lives in the source repository’s `examples/` directory. Obtain this repository, then install and run the example from its **root directory**:

```sh
python -m pip install --no-deps .
python -m examples.counter
```

Counter prints live text frames and its final memory snapshot. Its complete event codec, memory publisher, and snapshot store provide a starting point for custom ports.

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

For browser interaction, run the [SQLite Demo](demos/sqlite/README.en.md) to observe progress, failures, and refresh recovery. It uses synthetic data and dedicated SQLite and requires macOS/Linux. Repository examples and the Demo are excluded from wheel/sdist.

## Connect your application

After creating one execution, assemble `StreamRuntime` with three implementation objects: `EventCodec` turns business values into events, `LivePublisher` publishes live events, and `ArchiveStore` saves progress and the final result. Runtime inheritance is unnecessary. Start with the complete [Counter](examples/counter.py), then read [Architecture and integration](docs/architecture.en.md) and the [API](docs/api.en.md).

![Task calls Runtime; the host supplies three ports; the UI reads live events and snapshots](docs/diagrams/architecture.en.png)

| What it provides | What the application supplies |
| --- | --- |
| Bounded buffering, batch archiving (checkpoint), and notification after result commit | Scheduling, user permissions, transactions, execution ownership checks, and retries |
| Live reads, synchronous SSE encoding, and reader-iterator cleanup | HTTP responses, snapshot queries, and reconnect read/handoff logic |
| Custom events, live channels, and snapshot stores | A history-retention policy; the core does not guarantee complete history, lossless reconnects, or exactly-once delivery |

| Installation | Purpose and dependencies |
| --- | --- |
| Core wheel | Standard library; Runtime, ports, archive helpers, and synchronous SSE bodies |
| Repository Counter / SQLite Demo | Standard library + installed core |
| `[json]` | Default JSON events/codecs and Redis-cursor reader; Pydantic ≥2.10,<3 |
| `[redis]` | Adds the Redis live-store adapter; Pydantic ≥2.10,<3 and redis-py ≥3.5.3 |

[Demo](demos/sqlite/README.en.md) · [Performance charts](docs/performance.en.md) · [Choosing a solution](docs/comparison.en.md) · [Migration](docs/migration.en.md) · [Changelog](CHANGELOG.en.md) · [Contributing](CONTRIBUTING.en.md) · [Security](SECURITY.en.md) · [Releasing](docs/releasing.en.md)

CI covers Python 3.11–3.14 on Linux/macOS plus quality, security, and distribution checks; see [check scope](CONTRIBUTING.en.md#development-checks).
[Full MIT license](LICENSE) · [Provenance and upstream declaration](docs/license.en.md)

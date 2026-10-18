# ui-stream

A synchronous coordinator for bounded UI events, latest-state snapshots, and
host-owned durable results. Distribution name: `ui-stream`; import: `ui_stream`.
An alpha library maintained by [raja (@0RAJA)](https://github.com/0RAJA).
[CI](https://github.com/0RAJA/ui-stream/actions/workflows/ci.yml) validates source,
wheel and source distributions on Linux/macOS before release artifacts are published.

## Quick start

From a built wheel (the first PyPI release is being prepared):

```sh
python -m pip install --no-deps ./dist/ui_stream-0.1.0a1-py3-none-any.whl
python -m ui_stream.example
python -m ui_stream.demo --database ./demo.sqlite3 --port 8765
```

Open `http://127.0.0.1:8765`. The Demo uses only Python's standard library and
synthetic data. It binds to localhost and keeps the latest snapshot in a dedicated
SQLite file. See [Demo instructions](https://github.com/0RAJA/ui-stream/blob/main/src/ui_stream/demo/README.md).
The base package has no third-party runtime dependencies. Optional profiles are
installed with `ui-stream[json]` or `ui-stream[redis]` after publication.

```python
from ui_stream import StreamPolicy
from ui_stream.example import make_counter

runtime, channel, store = make_counter(policy=StreamPolicy(checkpoint_event_count=1))
runtime.send(4)
runtime.send(9)
runtime.finish(status="DONE", completion=13)
assert channel.frames == [b"tick:4", b"tick:9", b"done:DONE"]
assert store.snapshot.latest == 9 and store.snapshot.result == 13
```

## Guarantees and host responsibilities

- A Runtime belongs to one execution and serializes calls within the process.
- Pending events have explicit byte/count bounds. Successful checkpoints clear pending;
  known infrastructure failures degrade integrity and preserve bounded pending state.
- `finish` asks the host store to atomically commit tail, terminal event and result
  before closing and attempting live terminal publication.
- A failed commit propagates, leaves the Runtime open, and emits no terminal notification.
- An optional frozen summary callback runs once after closure, outside the Runtime lock.
- The host provides real commit boundaries, execution fencing, authentication,
  authorization, task retries/restarts, cancellation, retention and final business results.

This is a snapshot plus best-effort live-tail component. It does not promise complete
history replay, lossless reconnects, exactly-once delivery, cross-process synchronization,
async execution, crash recovery of computation, or task cancellation on reader disconnect.
The Demo is a local learning tool, without authentication or multi-tenant isolation.

## Dependencies and support

| Profile | Dependencies |
| --- | --- |
| Core, counter, SQLite Demo | Standard library only |
| `ui-stream[json]` | Pydantic >=2.10,<3 |
| `ui-stream[redis]` | Pydantic >=2.10,<3 and redis-py >=3.5.3 |
| Build | setuptools >=77.0.3 |
| Optional browser-client unit tests | Existing Node, no npm packages |

Python >=3.11 is required. CI targets CPython 3.11–3.14 on Linux and macOS;
check the linked CI run for the actual tested commit and successful combinations. The Demo requires
macOS/Linux `fcntl`; Windows Demo support is not provided. Redis integration tests use
fixtures, not a complete server/version compatibility matrix. Redis versions beyond
the locally exercised dependency versions require downstream compatibility checks.

## Development and documentation

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python tools/build_local.py --output ./dist
python tools/check_release.py --project . --dist ./dist
```

The full suite needs the declared optional JSON/Redis dependencies already present.
The standard-library Demo suite can run alone:
`PYTHONPATH=src python -m unittest tests.test_demo`.
Use a virtual environment. The local helper calls an existing backend without fetching
dependencies; CI installs declared test/build dependencies in its ephemeral runner. [Contributing](https://github.com/0RAJA/ui-stream/blob/main/CONTRIBUTING.md) explains source and wheel testing.

- [Architecture and extension ports](https://github.com/0RAJA/ui-stream/blob/main/docs/architecture.md)
- [API contract and compatibility](https://github.com/0RAJA/ui-stream/blob/main/docs/api.md)
- [Demo](https://github.com/0RAJA/ui-stream/blob/main/src/ui_stream/demo/README.md)
- [Security and vulnerability reporting](https://github.com/0RAJA/ui-stream/blob/main/SECURITY.md)
- [Release checklist](https://github.com/0RAJA/ui-stream/blob/main/docs/releasing.md)
- [Changelog](https://github.com/0RAJA/ui-stream/blob/main/CHANGELOG.md)
- [Provenance](https://github.com/0RAJA/ui-stream/blob/main/PROVENANCE.md), [MIT license](https://github.com/0RAJA/ui-stream/blob/main/LICENSE), [upstream declaration](https://github.com/0RAJA/ui-stream/blob/main/LICENSE.upstream)

The public repository is [0RAJA/ui-stream](https://github.com/0RAJA/ui-stream).
Use [private vulnerability reporting](https://github.com/0RAJA/ui-stream/security/advisories/new)
for sensitive reports; see SECURITY.md. No private email or publication token is bundled.

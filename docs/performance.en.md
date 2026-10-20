# Performance baseline

[简体中文](performance.md) · [Documentation](README.en.md)

On these local short bursts, the worst event-to-parse p99 is **2.897 ms** and the short-lived worker's peak RSS is **56.375 MiB**. Both sit inside the **50 ms** and **128 MiB** budgets selected for this report. For the same 512 events, the SQLite producer interval is about **13.1×** the volatile-state interval.

The samples measure transport, core coordination, and SQLite cost. Production capacity, an SLO, and sustained Demo behavior need separate measurement; the boundary is at the end of this page.

## Results

The budget matches this use: one finite task, a 128-byte message field plus JSON/SSE metadata, an update every **500 ms** (2 Hz), and at most eight local observers. Ten percent of that 500 ms, **50 ms**, is reserved for the additional latency from emitting an event until it is parsed. The complete short-lived worker has a peak-RSS budget of **128 MiB**. These are the engineering budgets selected for this report.

| Item | Budget | Measured |
| --- | --- | --- |
| Event to parse | 50 ms | Worst per-run p99 **2.897 ms** (**5.8%** of the budget); largest individual observation **2.956 ms** |
| Short-lived worker memory | 128 MiB RSS | Maximum runtime HTTP peak RSS **56.375 MiB** |
| Producer interval for 512 events | No separate time budget in this report | Runtime medians: volatile state **5.80 ms**, SQLite **75.68 ms**, about **13.1×** |

The latency and memory rows come from nine runtime HTTP/SQLite samples: one, four, and eight readers, three repetitions each. They delivered **2,496** ordered business frames and **39** terminals. 64 events × (1+4+8) readers × 3 repetitions = 2,496 frames; (1+4+8) × 3 repetitions = 39 terminals, one per reader. Results were committed by completion. Peak RSS includes the interpreter, optional modules, SQLite, the server, and the local clients.

## Workloads

`runtime` is the full `StreamRuntime`. `raw` uses the same codec, live/archive JSON encoding, cursors, one frame consumption per event, a checkpoint every 16 events, and terminal publication after commit. Checksums, counts, batches, and results verify equal work. `raw` omits locking, capacity checks, integrity tracking, and recovery, and does not provide equivalent failure guarantees.

| Layer | Work | Persistence | Compared paths |
| --- | --- | --- | --- |
| Core | 512 progress events, 128 ASCII text-field bytes plus metadata, one producer, synchronous frame-checksum sink | Volatile state / SQLite latest snapshot | `StreamRuntime` / raw loop |
| SSE encoding | 512 identical compact JSON payloads, `id: <step>-0`, LF frames | No network, flush, or archive | ui-stream JSON/SSE profile / installed sse-starlette `ServerSentEvent` |
| HTTP + SQLite | 64 events, one producer, 1/4/8 localhost readers | Latest snapshot; atomic tail, terminal, and result | Runtime / raw producer on the same host and adapters |

HTTP reuses the Demo's handler, MemoryChannel, and SQLite repository. The benchmark generates the events. Production starts after every reader is ready, every frame is flushed, and each reader must receive ordered business frames plus one terminal. Results cover a single-process standard-library host, local clients, and SQLite.

The charts use the benchmark settings: backlog=8, no pacing, checkpoint=16. The Demo uses backlog=5, checkpoint=3, a 500 ms update interval, and at most four jobs. The charted rates are unpaced bursts, not that sustained Demo run. The benchmark also does not apply `DemoService`'s four-job admission limit.

## How to read the charts

Each case repeats three times in a fresh subprocess, alternating forward and reverse order, with no separate warmup and no per-event sleep. The first operation is cold and later operations warm naturally. The bounded burst is there to expose queueing.

Dots are the three raw results, the center line is their median, and whiskers are the minimum and maximum. Connecting lines only guide the eye. Nearest-rank p50/p95/p99 are computed inside each sample. The three repetitions are neither pooled nor averaged as percentiles. Whiskers are the range of those three runs, not a confidence interval.

## Figure 1: latency as readers increase

![Slowest first frame and per-run delivery p95 for one, four and eight readers, milliseconds](performance-figures/http-latency.png)

Latency rises from one reader to eight, and the raw and runtime ranges stay broadly similar. Each reader requires frame encoding, sending, and parsing. Scheduling, encoding, and queueing were not separated with a profiler.

The x-axis is the measured **1, 4, and 8** readers. The y-axis is **ms, lower is better**. The first frame runs from the start of the HTTP request to parsing the first business frame, including connection setup and the ready barrier, excluding heartbeats. A run has only one, four, or eight first-frame observations, so the first-frame p95 on the chart is the slowest observer. Delivery p95 belongs to each sample.

## Figure 2: unique events and aggregate deliveries

![Unique-event and aggregate-frame throughput over the complete HTTP wave](performance-figures/http-throughput.png)

Runtime medians are **4,266 → 3,096 → 2,575 events/s** and **4,266 → 12,383 → 20,600 frames/s**.

The left panel is unique business events per second. The event count is fixed, so a lower rate means a longer wave from the earliest request to the last reader finishing. The right panel is delivered business frames per second: one event sent to eight observers counts as eight frames, so the numerator grows with reader count and the right panel rises. Terminals count toward wave duration and are excluded from both numerators. At a fixed reader count, a higher value means that wave finished sooner.

## Figure 3: SQLite and volatile-state costs

![Producer interval for the same 512 events with volatile and SQLite storage, milliseconds](performance-figures/core-persistence.png)

For the same 512 events, the runtime median moves from **5.80 ms** with volatile state to **75.68 ms** with SQLite, about **13.1×**. The raw and runtime SQLite ranges overlap.

The y-axis is the producer interval in **ms, lower is better**. Every 16th event opens a connection and commits a checkpoint, so about 6.25% of sends include connection, encoding, and transaction work. Per-send p50 stays in microseconds, while p95 includes that millisecond commit. The 13.1× ratio is the whole storage path; disk, caches, SQLite, encoding, and locking were not isolated.

Each sample uses a fresh temporary database that keeps only the latest snapshot. Each checkpoint is a short transaction on its own connection, and the final result commits atomically. The journal mode is WAL, and the historical results record `synchronous=1` (NORMAL). That is a different durability guarantee from FULL: a power loss can drop committed transactions. See [SQLite synchronous semantics](https://www.sqlite.org/pragma.html#pragma_synchronous). Schema creation, execution creation, and final verification sit outside this 512-event producer interval. Volatile values disappear when the process exits.

## What these numbers cover

The samples can be checked for three things:

- Whether the selected local short bursts stayed inside 50 ms and 128 MiB.
- The total cost of this SQLite path relative to volatile state.
- How latency, unique-event throughput, and aggregate delivery change from one reader to eight.

The following need their own acceptance runs:

- Latency while a Demo serves eight observers continuously at 2 Hz, long-lived memory growth, browsers, remote networks, computation, and recovery time.
- `finish` while business events are still waiting for a checkpoint. Both 512 and 64 are divisible by checkpoint=16, so the samples have no such tail.
- TLS, proxies, a Redis server, remote databases, multiprocess fanout, slow clients, and timings taken after SQLite or kernel caches are flushed.
- Confidence intervals, tail SLOs, saturation capacity, and production sizing. The measurement is three short ASCII bursts on one machine, with one writer. Scheduling, power, temperature, and other applications affect the results.
- ASGI networking, and ranking frameworks by speed. The encoding comparison uses only the installed sse-starlette 3.2.0 and compares equivalent bytes. Responsibilities and recovery boundaries are compared in [Choosing a solution](comparison.en.md).

## Metric definitions

- **First event:** time until the first synchronous operation completes for core and encoding. For HTTP, time from request start until the first business frame is parsed, including connection setup and the ready barrier, excluding heartbeats.
- **Latency:** `send_ns` includes synchronous checkpoints. HTTP `delivery_ns` runs from event construction before `send()` to a complete client parse of the frame, on one monotonic clock, including queueing, encoding, sockets, and parsing.
- **Percentiles:** nearest-rank p50/p95/p99 within each sample. Three repetitions are neither pooled nor averaged as percentiles. A core first-event sample has one observation, so its three percentiles are equal.
- **Throughput:** the producer interval is the denominator for core and encoding. The complete wave is the denominator for HTTP, which reports both unique events and delivered frames (events × readers). Terminals count toward elapsed time and are excluded from the numerator.
- **Memory and disk:** peak RSS of a fresh worker includes the interpreter, optional imports, SQLite, the server, and local clients. It is not the incremental cost of core. `database_bytes` is the database plus WAL size at the end, not bytes written and not the peak size.

## Reproduction

All **36** samples are in the [raw JSON](../benchmarks/results/baseline-2026-10-18.json): 2026-10-18, Python 3.11.10, SQLite 3.54.0, Apple M3 Pro (12 logical CPUs, 36 GiB), arm64, macOS 27.0.1. Optional encoding dependencies were Pydantic 2.10.4 and sse-starlette 3.2.0.

This is the historical macOS measurement for immutable [source `7248096`](https://github.com/0RAJA/ui-stream/tree/72480966c9bb154362b2a378ec24eab633205f7b). The raw bytes and source digest are kept. Later code and Linux need their own measurements. The [derived series](performance-figures/series.json) keep the original values, the statistical definitions, and the source file's SHA256. Each PNG has a same-named SVG.

Run from the repository root with a Python environment that already has the core package installed. The scripts do not install dependencies. Benchmarks, Demos, and examples stay out of the wheel and sdist. Write new measurements to a separate file; leave this historical baseline in place.

```bash
python -m benchmarks.run --smoke --output benchmarks/results/smoke-local.json
python -m benchmarks.run --output benchmarks/results/baseline-local.json
python -m unittest tests.test_benchmarks -v
```

Optional plotting needs ReportLab already installed. On macOS, system `sips` exports PNG; without `sips`, the tool still writes SVG. `--pdf-output` can place the PDF outside the repository. Core, Demo, and benchmark runs do not need ReportLab.

```sh
python -m benchmarks.plot --input benchmarks/results/baseline-2026-10-18.json --output docs/performance-figures
```

Hard bounds: 1,000 core or encoding events, 256 HTTP events, eight readers, 4,096 payload bytes, three connection levels, and three repetitions. A worker may use at most 30 seconds of wall time, 15 seconds of CPU, and 128 file descriptors. Linux caps address space at 512 MiB, and the suite stops scheduling after 120 seconds. macOS bounds memory through the workload, the readers, and channel capacity, with no separate address-space cap. Smoke uses 16 core/HTTP events, one connection, and two repetitions. Tests set no machine-speed threshold.

The current Linux launcher sets `MALLOC_ARENA_MAX=2` in the child environment before Python starts. That limits glibc arena reservations while keeping the 512 MiB cap and the 1/4/8 reader coverage. See the [GNU libc documentation](https://sourceware.org/glibc/manual/latest/html_node/Memory-Allocation-Tunables.html). New results record the actual limits and allocator environment in `worker_limits`. This historical macOS JSON predates those fields.

Samples use synthetic data and loopback only, and temporary databases are removed. The JSON records environment versions and a relative-source AST digest. It does not record hostnames, usernames, absolute interpreter paths, or local paths.

# Performance baseline

[简体中文](performance.md) · [Documentation](README.en.md)

These local synthetic bursts measure transport, core coordination, and SQLite costs. They do not establish production capacity or an SLO.

## Results and scope

For one finite task with 128-byte message fields (plus JSON/SSE metadata), a 500 ms update period (2 Hz), and up to eight local observers, allocate 10% of the period, **50 ms**, to additional event-to-parser latency and **128 MiB** RSS to the complete short-lived worker. These are the engineering budgets selected for this report.

Nine runtime HTTP/SQLite samples delivered **2,496** ordered business frames and **39** terminals, with results committed by completion. The worst per-run p99 was **2.897 ms** (**5.8%** of the budget), the largest individual latency was **2.956 ms**, and the maximum runtime HTTP peak RSS was **56.375 MiB**. These short bursts met the selected transport and memory budgets.

The actual Demo at sustained 2 Hz / eight observers, long-lived memory growth, browser and remote-network behavior, computation, and recovery timing need separate acceptance. The Demo uses backlog=5/checkpoint=3; the benchmark uses backlog=8/no pacing/checkpoint=16. Its 512/64 events are multiples of checkpoint=16, so it does not measure finishing with a non-empty business tail.

## Figure 1: latency as readers increase

![Slowest first frame and per-run delivery p95 for one, four and eight readers, milliseconds](performance-figures/http-latency.png)

The x-axis contains measured **1, 4, and 8** readers; the y-axis is **ms, lower is better**. Dots are three raw results, the center line is their median, and whiskers show min–max; connecting lines only guide the eye. Each run has 1/4/8 first-frame observations, so first-frame p95 equals the slowest observer. Delivery p95 belongs to each run; samples are not pooled.

Latency rises with reader count on this reference path, with broadly similar raw/runtime ranges. Each reader requires frame encoding, sending, and parsing; scheduling, encoding, and queueing contributions have not been profiled.

## Figure 2: unique events and aggregate deliveries

![Unique-event and aggregate-frame throughput over the complete HTTP wave](performance-figures/http-throughput.png)

The x-axis is reader count. The left panel shows **unique business events/s**, the right **delivered business frames/s**; both divide by the complete wave from earliest request to last reader completion. One event sent to eight observers counts as eight frames; terminals are excluded from the numerator. Higher values mean this fixed workload finished sooner.

Runtime medians are **4,266 → 3,096 → 2,575 events/s** and **4,266 → 12,383 → 20,600 frames/s**. Aggregate growth includes the fanout multiplier; it does not show the producer becoming faster with concurrency.

## Figure 3: SQLite and volatile-state costs

![Producer interval for the same 512 events with volatile and SQLite storage, milliseconds](performance-figures/core-persistence.png)

The y-axis is **producer interval in ms for the same 512 events, lower is better**, showing three raw observations and their median/min–max. The runtime median changes from **5.80 ms** with volatile state to **75.68 ms** with SQLite, a ratio of about **13.1×**. Raw/runtime SQLite ranges overlap.

Every 16th event opens a connection and commits a checkpoint. About 6.25% of sends therefore include connection, encoding, and transaction work: p50 remains in microseconds while p95 includes millisecond work. The 13.1× ratio is the total cost of this storage path; disk, caches, SQLite, encoding, and locking were not isolated.

## Provenance and reproduction

All **36** samples remain in the [raw JSON](../benchmarks/results/baseline-2026-10-18.json): 2026-10-18, Python 3.11.10, SQLite 3.54.0, Apple M3 Pro (12 logical CPUs, 36 GiB), arm64, macOS 27.0.1. Optional encoder dependencies were Pydantic 2.10.4 and sse-starlette 3.2.0.

This historical macOS measurement belongs to immutable [source `7248096`](https://github.com/0RAJA/ui-stream/tree/72480966c9bb154362b2a378ec24eab633205f7b). Its raw bytes and source hashes are preserved; it is neither a rerun of later code nor a Linux measurement. [Derived series](performance-figures/series.json) retain values, statistical definitions, and the raw file SHA256. Each PNG has a same-name SVG.

Run from the repository root using a Python environment with the core installed. Scripts do not install dependencies; benchmarks, Demos, and examples remain outside wheel/sdist. Write new measurements to separate files rather than overwriting the historical baseline.

```bash
python -m benchmarks.run --smoke --output benchmarks/results/smoke-local.json
python -m benchmarks.run --output benchmarks/results/baseline-local.json
python -m unittest tests.test_benchmarks -v
```

The optional plot tool needs existing ReportLab. macOS uses system `sips` to export PNG; without it, the tool still writes SVG. `--pdf-output` can send PDFs outside the repository. Core, Demo, and benchmark execution do not need ReportLab.

```sh
python -m benchmarks.plot --input benchmarks/results/baseline-2026-10-18.json --output docs/performance-figures
```

## Workload and controls

| Layer | Work | Persistence | Compared paths |
| --- | --- | --- | --- |
| Core | 512 progress events, 128 ASCII text-field bytes plus metadata, one producer, synchronous frame-checksum sink | Volatile state / SQLite latest snapshot | `StreamRuntime` / raw loop |
| SSE encoding | 512 identical compact JSON payloads, `id: <step>-0`, LF frames | No network, flush, or archive | ui-stream JSON/SSE profile / installed sse-starlette `ServerSentEvent` |
| HTTP + SQLite | 64 events, one producer, 1/4/8 localhost readers | Latest snapshot; atomic tail, terminal, and result | Runtime / raw producer on the same host/adapters |

Raw/runtime use the same codec, live/archive JSON encoding, cursors, one frame consumption per event, checkpoint batch of 16, and terminal publication after commit. Checksums, counts, batches, and results verify equal work. Raw omits locking, capacity checks, integrity tracking, and recovery; it does not provide equivalent failure guarantees.

Each SQLite sample uses a fresh temporary database retaining only the latest snapshot, a separate connection and short transaction per checkpoint, and an atomic final result transaction. WAL and the recorded `synchronous=1` (NORMAL) setting do not provide FULL power-loss durability; committed transactions may be lost on power failure. See [SQLite synchronous semantics](https://www.sqlite.org/pragma.html#pragma_synchronous). Schema/execution creation and final verification fall outside the timed core producer interval. Volatile values disappear when the worker exits.

HTTP reuses the Demo's actual handler, MemoryChannel, and SQLite repository with benchmark-generated synthetic events, not the paced `DemoService` or its four-job admission limit. Production starts after all readers are ready; every frame is flushed, and assertions require ordered business frames and one terminal per reader. Results cover a single-process standard-library host, local clients, and SQLite.

## Metric definitions

- **First event:** first synchronous operation for core/encoding; request start to parsing the first business frame for HTTP, including connection setup and the admission barrier. Heartbeats are excluded.
- **Latency:** `send_ns` includes synchronous checkpoints. HTTP `delivery_ns` runs from event construction before `send()` to complete client parsing on one monotonic clock, including queueing, encoding, sockets, and parsing.
- **Percentiles:** nearest-rank p50/p95/p99 within each sample; three repetitions are neither pooled nor averaged as percentiles. A core first-event sample has one observation, so all three percentiles are equal.
- **Throughput:** producer interval for core/encoding; complete wave for HTTP, reporting both unique events and delivered frames (events × readers). Terminals count toward elapsed time, not the numerator.
- **Memory/disk:** fresh-worker peak RSS includes Python, optional imports, SQLite, server, and local clients; it is not incremental core memory. `database_bytes` is final database/WAL size, not bytes written or peak usage.

## Resource bounds and limitations

Each case repeats three times in fresh subprocesses, alternating forward/reverse order, without explicit warmup or per-event sleep. The first operation is cold and later operations warm naturally; bounded bursts expose queueing.

Hard bounds: 1,000 core/encoder events, 256 HTTP events, eight readers, 4,096 payload bytes, three connection levels, and three repetitions. Workers have 30 wall seconds, 15 CPU seconds, and 128 file descriptors; Linux caps address space at 512 MiB. The suite stops scheduling after 120 seconds. macOS memory is bounded by workloads, readers, and channel capacity without an address-space limit. Smoke uses 16 core/HTTP events, one reader, and two repetitions; tests set no machine-speed thresholds.

The current Linux launcher sets `MALLOC_ARENA_MAX=2` in the child environment before Python starts, limiting glibc arena reservations while retaining the 512 MiB cap and 1/4/8-reader coverage. See [GNU libc documentation](https://sourceware.org/glibc/manual/latest/html_node/Memory-Allocation-Tunables.html). New results record actual limits and allocator settings in `worker_limits`; the historical macOS JSON predates these fields.

Only synthetic data and loopback sockets are used; temporary databases are removed. JSON records environment versions and relative-source AST hashes, excluding hostnames, usernames, executable locations, and local paths.

This covers one machine, short ASCII bursts, and one writer, without TLS, proxies, browsers, a Redis server, remote databases, multiprocess fanout, or slow-client fault injection. SQLite/kernel caches are not flushed. Scheduling, power/thermal state, and other applications affect results. Three short runs cannot establish confidence intervals, tail SLOs, saturation capacity, or production sizing.

Only installed sse-starlette 3.2.0 participates in equal-byte encoding comparisons; this report does not test ASGI networking or rank frameworks. [Choosing a solution](comparison.en.md) separately compares responsibilities and recovery boundaries.

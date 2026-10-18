# Performance: reproducible first baseline

[中文](performance.md)

This first benchmark answers where time is spent for a small synthetic stream. It does not establish a production capacity limit. The core coordinates caller-owned event, publisher and archive ports; HTTP hosting and storage are adapter choices.

## Read this first: transport fits the local budget; overall acceptance is conditional

This is an **engineering acceptance example chosen for maintenance**, not an industry-wide threshold: one finite task, 128-byte message fields (plus JSON/SSE metadata), an update every 500 ms (2 Hz), and up to eight local observers. Allocate 10% of the update period, **50 ms**, to additional event-to-parser latency, and **128 MiB** RSS to the complete short-lived worker. Browser painting, remote networking, computation, and recovery timing need separate acceptance.

The nine historical runtime HTTP/SQLite samples fit this transport budget in every measured burst: the worst per-run p99 was **2.897 ms**, or **5.8%** of the allowance; the largest individual observed latency was **2.956 ms**. This supports continuing local functional acceptance. The data measures bursts, not the paced 2 Hz Demo, so **overall acceptance is conditional; a production SLO is not established**.

| Acceptance item | Explicit budget or correctness requirement | Evidence / verdict |
| --- | --- | --- |
| Reference-host burst completeness | Every reader receives ordered business frames and one terminal; result committed by completion | 2,496 business frames and 39 terminals across nine runtime samples; all assertions passed |
| Additional runtime reference-host delivery latency | Each run p99 ≤50 ms | Worst 2.897 ms; passes this sub-budget, not a sustained tail-latency promise |
| Short-worker memory | Peak RSS ≤128 MiB, including server, local clients, and optional imports | Runtime HTTP maximum 56.375 MiB; short-run pass, long-lived growth unverified |
| Actual Demo at 2 Hz / eight admissions | Preserve 500 ms cadence with eight simultaneous observers | Unverified: actual Demo backlog=5/checkpoint=3; benchmark backlog=8/unpaced/checkpoint=16 |
| Disconnect / restart recovery timing | Product must first define a recovery-time budget | Not timed here; functional recovery/rollback tests do not establish timing acceptance |
| Non-empty-tail finish and power-loss durability | Cover the relevant final transaction and durability mode | 512 and 64 are multiples of checkpoint=16, leaving no business pending at finish; WAL/NORMAL is not FULL power-loss durability |

## Figure 1: how latency changes with readers

![Slowest first frame and per-run delivery p95 for one, four and eight readers, milliseconds](performance-figures/http-latency.png)

The x-axis contains only the measured **1, 4, and 8** readers; the y-axis is **ms, lower is better**. Each group retains three observations, a median line and min–max whiskers. Connecting lines guide the eye; they add no observations or fitted points. A run has only 1/4/8 first-frame observations, so its first-frame p95 equals the slowest observer and cannot establish a robust tail SLO. Delivery p95 belongs to each individual run; the three runs are not pooled.

**Observed:** this reference path's latency rises with reader count, with broadly similar raw/runtime ranges on the same host. **Explanation:** code encodes, sends, and parses frames for each reader, adding thread/socket work. Without profiling, the contributions of scheduling, encoding, and queueing remain hypotheses; all growth cannot be assigned to the runtime.

## Figure 2: more deliveries do not mean a faster producer

![Unique-event and aggregate-frame throughput over the complete HTTP wave](performance-figures/http-throughput.png)

Both panels use actual reader counts. The left is **unique business events/s**; the right is **delivered business frames/s**. Both divide by the complete wave, from earliest request to last reader completion. One event sent to eight observers counts as eight frames. Terminals are excluded from the numerator. Higher values mean this fixed workload finished sooner, not a supported connection limit.

Runtime median unique throughput is about **4,266 → 3,096 → 2,575 events/s**, while aggregate delivery is **4,266 → 12,383 → 20,600 frames/s**. The right panel includes the fanout multiplier. It does not show the core becoming faster at higher concurrency; overlapping three-run raw/runtime ranges do not establish a winner.

## Figure 3: separate SQLite cost from core coordination

![Producer interval for the same 512 events with volatile and SQLite storage, milliseconds](performance-figures/core-persistence.png)

The y-axis is **producer interval in ms for the same 512 events, lower is better**; all three observations, medians and min–max ranges remain visible. The runtime median changes from **5.80 ms** with volatile state to **75.68 ms** with SQLite, about **13.1×** as a ratio of group medians. Raw SQLite ranges overlap runtime SQLite ranges.

**Known structure:** every 16th event opens a connection and commits a checkpoint, adding connection, encoding, and transaction work. About 6.25% of sends include a checkpoint, explaining why p50 remains in microseconds while p95 includes millisecond work. **Not isolated:** how much comes from disk, caches, SQLite, encoding, or locking. The entire 13.1× difference is not runtime overhead, and these short runs cannot rank frameworks.

## Reproduce the figures and inspect provenance

Figures derive from the unchanged [36-sample raw JSON](../benchmarks/results/baseline-2026-10-18.json), tied to immutable source `7248096`; they are not new measurements of this review's fixes. [Derived series](performance-figures/series.json) retain original values, statistics and the raw file SHA256. Each PNG has a same-name SVG alongside it; no external image service is required.

The optional maintenance tool below uses existing ReportLab; macOS uses system `sips` for PNG export. Without `sips`, it still writes SVG. Normal core, Demo and benchmark execution do not need ReportLab. Optional `--pdf-output` exports PDFs outside the repository. Do not overwrite the historical baseline by measuring while a user is accepting the Demo or the machine is busy.

```sh
python -m benchmarks.plot --input benchmarks/results/baseline-2026-10-18.json --output docs/performance-figures
```

## Reproduce

Run from a repository checkout with the core installed in your existing Python environment. The harness, `demos/` and `examples/` remain outside the wheel and source distribution. No benchmark dependency is installed automatically.

```bash
python -m benchmarks.run --smoke --output benchmarks/results/smoke-local.json
python -m benchmarks.run --output benchmarks/results/baseline-local.json
python -m unittest tests.test_benchmarks -v
```

Use your environment's Python executable. The saved canonical run is [baseline-2026-10-18.json](../benchmarks/results/baseline-2026-10-18.json); its environment and per-sample raw timings are authoritative. Re-run the command to evaluate another machine. An optional `sse-starlette` encoder is measured only when already installed; the default core/HTTP cases need only the standard library and the installed core.

The canonical JSON is a historical macOS measurement of immutable [source commit `7248096`](https://github.com/0RAJA/ui-stream/tree/72480966c9bb154362b2a378ec24eab633205f7b). Its raw bytes and recorded source hashes are preserved. The later Linux worker allocator policy changes the current harness; a fresh run records the current source fingerprint rather than replacing or relabeling that historical baseline. No Linux performance measurement is claimed by the macOS result.

## Workload and boundaries

| Layer | Work | Persistence | Compared paths |
| --- | --- | --- | --- |
| Core microbenchmark | 512 progress events, 128 ASCII text-field bytes (plus event metadata), one producer, synchronous frame checksum sink | Separate volatile and SQLite latest-snapshot cases | `StreamRuntime` and a raw loop |
| SSE encoding | 512 identical compact JSON payloads, `id: <step>-0`, LF framing | None; no network, flush or archive | ui-stream JSON/SSE profile and installed sse-starlette `ServerSentEvent` |
| HTTP + SQLite | 64 events, one producer, 1 → 4 → 8 simultaneous readers on localhost | SQLite latest snapshot; tail, terminal and result in one final transaction | Same HTTP host/adapters, runtime and raw producer |

Raw and runtime use the same event codec, live/archive JSON encoding, cursor assignment, one frame consumption per event, checkpoint batch of 16, and terminal publication after commit. Both encode the live and archive views of every event. The raw loop intentionally omits runtime locking, capacity checks, integrity tracking and recovery. This isolates their normal-path cost; the raw loop is not a replacement with the same failure guarantees. Checksums, counts, batch sizes and final results verify equal work.

SQLite cases use the demo repository's WAL mode, the connection's default synchronous setting (recorded by the harness), a fresh temporary database, one short transaction and connection per checkpoint, and an atomic final result transaction. The archive retains the latest snapshot, not every historical event. The canonical run records `synchronous=1` (NORMAL). This measures transaction commits, not FULL-mode power-loss durability; WAL/NORMAL may lose a committed transaction after power loss. See [SQLite synchronous semantics](https://www.sqlite.org/pragma.html#pragma_synchronous). Initial schema/job creation and final verification are outside the timed core producer interval. There is no persistence in the volatile case: its latest value and result disappear with the worker.

The HTTP fixture reuses `demos.sqlite`'s actual HTTP handler, memory channel and SQLite repository, with a benchmark-owned synthetic producer. It does not run the paced `DemoService` job or its four-job admission limit. All readers are admitted before production, each frame is flushed, and every reader must receive all ordered progress frames and one terminal frame. These HTTP results describe a single-process standard-library host plus local clients and SQLite. They cannot describe an ASGI deployment or a maximum supported core connection count.

The benchmark host explicitly uses a listen backlog of 8; the unmodified demo defaults to 5. Raw/runtime share this configuration. An initial unrecorded attempt exposed connection resets at the default backlog and an admission-cleanup deadlock; the fixture now releases readers before joining them on failure. Failed attempts are not included as successful samples.

## Metrics

- **First event:** core/encoding measure the first synchronous operation; HTTP measures request start to parsing the first business frame. HTTP includes the reader-admission barrier and connection setup. A heartbeat is never counted as a first business event.
- **Latency:** raw `send_ns` samples include any synchronous checkpoint. HTTP `delivery_ns` measures event construction immediately before `send()` to complete frame parsing on each client, using the same monotonic clock. It includes queueing, JSON/SSE encoding, socket transfer and client parsing.
- **Percentiles:** nearest-rank p50/p95/p99 within each sample. Three repetitions remain separate. A single core first-event observation has identical p50/p95/p99; it is not a distribution with 512 observations. Do not average percentiles as though they were pooled measurements.
- **Throughput:** business events divided by the producer interval for core/encoding; HTTP uses the earliest request start through the latest reader completion. HTTP reports unique producer events/s and delivered events/s (events × readers); terminal frames are excluded from the numerator and included in elapsed time.
- **Memory:** process peak RSS, including Python, imported optional modules, SQLite, server and local clients. Fresh workers prevent prior cases contaminating this high-water mark. It is not incremental core memory or a client-only/server-only measurement. `database_bytes` is final on-disk database/WAL size, not bytes written or peak disk usage.
- **Persistence cost:** compare the same core implementation's elapsed interval with and without SQLite. Connection/transaction latency, snapshot encoding and filesystem cache state contribute. The report does not attribute this whole difference to the core.

## Resource bounds and sampling

Defaults: three repetitions; fresh subprocess per case; alternating forward/reverse case order; no explicit warmup, so the first operation is cold while later operations warm naturally. There is no per-event sleep or producer pacing. The bounded burst makes queueing visible.

Hard input bounds are 1,000 core/encoder events, 256 HTTP events, 8 readers, 4,096 payload bytes, three connection levels and three repetitions. Workers have a 30-second wall deadline, 15 CPU seconds and 128 file descriptors; Linux additionally caps address space at 512 MiB. The suite stops scheduling after 120 seconds. macOS has no portable address-space limit here; memory is bounded structurally by workload sizes, readers and channel capacity. The smoke preset is 16 core events / 16 HTTP events / 1 reader / 2 repetitions. Tests verify protocol, results and limits, with no machine-specific speed threshold.

On Linux, both the suite and protocol-test workers use one launcher that sets `MALLOC_ARENA_MAX=2` in a copied child environment **before Python starts**. This bounds glibc allocator arena reservations so thread creation does not consume the 512 MiB virtual-address budget merely through multiple arenas; it does not raise the address-space limit or reduce the tested 1/4/8 readers. The parent environment is unchanged. See the [GNU libc arena tunable documentation](https://sourceware.org/glibc/manual/latest/html_node/Memory-Allocation-Tunables.html). New worker results record the actual CPU/FD/address-space limits and allocator environment in `worker_limits`; the historical macOS JSON predates these fields.

Only synthetic data and loopback sockets are used. Temporary databases are removed. Public JSON records hardware/OS/Python/package versions and relative-source AST hashes (formatting/comments ignored), never hostnames, usernames, executable locations or local filesystem paths.

## Selection is separate from measurement

[Choosing a solution](comparison.en.md) compares StreamingResponse, sse-starlette, current FastAPI native SSE, Django EventStream, and LangGraph by responsibility and recovery boundary. Only the installed sse-starlette 3.2.0 participates in equal-byte encoding measurements. This is not an ASGI network test or a framework capacity ranking.

## Limits of this baseline

One local machine, short synthetic ASCII bursts, one writer and no TLS, reverse proxy, browser, Redis server, remote database, cross-process fanout or slow-client fault injection. SQLite and kernel caches are not flushed. Scheduling, power/thermal state and other local applications can change results. Three short repetitions support reproduction and diagnosis, not confidence intervals, tail-latency SLOs, saturation claims or production sizing. Increase load only within the bounds and on an otherwise quiet machine; change the harness explicitly for a separately reviewed workload.

## Recorded run

Recorded 2026-10-18, Python 3.11.10, SQLite 3.54.0, Apple M3 Pro (12 logical CPUs, 36 GiB RAM), arm64, macOS 27.0.1. All **36** samples are retained. Optional encoder versions: Pydantic 2.10.4 and sse-starlette 3.2.0. Each slash-separated cell is repetition 1 / 2 / 3; percentile cells are per repetition, not pooled.

| Layer / 层次 | Implementation / 实现 | Store / 存储 | Events/s · r1 / r2 / r3 | send p95 µs · r1 / r2 / r3 |
| --- | --- | --- | --- | --- |
| core | raw | none | 90,585 / 96,608 / 95,771 | 11.12 / 11.79 / 12.21 |
| core | runtime | none | 83,507 / 90,294 / 88,318 | 14.00 / 12.17 / 13.54 |
| core | raw | sqlite | 7,048 / 6,661 / 6,388 | 1,740.50 / 1,914.25 / 1,793.62 |
| core | runtime | sqlite | 7,032 / 6,765 / 6,496 | 1,642.92 / 1,726.17 / 1,946.67 |
| encoding | native | none | 298,804 / 320,760 / 271,570 | 4.12 / 3.08 / 4.29 |
| encoding | sse-starlette | none | 279,102 / 280,465 / 281,693 | 4.50 / 4.04 / 4.42 |

| HTTP / SQLite | Readers / 读取者 | Delivered events/s · r1 / r2 / r3 | First event p95 ms · r1 / r2 / r3 | Delivery p95 ms · r1 / r2 / r3 |
| --- | --- | --- | --- | --- |
| raw | 1 | 4,404 / 3,911 / 4,209 | 3.023 / 2.784 / 2.711 | 0.647 / 0.452 / 0.429 |
| runtime | 1 | 4,266 / 4,288 / 4,044 | 2.737 / 2.867 / 2.993 | 0.466 / 0.419 / 0.660 |
| raw | 4 | 13,755 / 12,635 / 13,520 | 4.123 / 4.452 / 3.979 | 1.500 / 1.638 / 1.551 |
| runtime | 4 | 12,035 / 12,728 / 12,383 | 5.472 / 4.946 / 5.192 | 1.709 / 1.565 / 1.599 |
| raw | 8 | 20,684 / 21,716 / 20,586 | 6.739 / 5.760 / 6.456 | 2.623 / 2.666 / 2.793 |
| runtime | 8 | 22,447 / 20,600 / 19,953 | 5.479 / 6.202 / 6.866 | 2.643 / 2.834 / 2.673 |

| Runtime latency / 延迟 | Unit / 单位 | r1 p50 / p95 / p99 | r2 p50 / p95 / p99 | r3 p50 / p95 / p99 |
| --- | --- | --- | --- | --- |
| core / none | µs | 10.042 / 14.000 / 17.833 | 9.708 / 12.166 / 18.542 | 9.750 / 13.541 / 17.292 |
| core / sqlite | µs | 11.125 / 1642.916 / 2323.791 | 11.000 / 1726.166 / 2584.917 | 11.167 / 1946.667 / 2533.125 |
| http / sqlite / 1 readers | ms | 0.290 / 0.466 / 0.589 | 0.278 / 0.419 / 0.560 | 0.282 / 0.660 / 0.679 |
| http / sqlite / 4 readers | ms | 1.055 / 1.709 / 1.925 | 0.960 / 1.565 / 1.684 | 0.946 / 1.599 / 1.701 |
| http / sqlite / 8 readers | ms | 1.692 / 2.643 / 2.870 | 1.860 / 2.834 / 2.897 | 1.682 / 2.673 / 2.825 |

Core runtime without persistence observed 83,507–90,294 events/s, compared with 90,585–96,608 for the equal-I/O raw loop. With SQLite the runtime observed 6,496–7,032 events/s, overlapping the raw loop's 6,388–7,048. Transaction work dominates this particular workload; these short runs do not establish a statistically significant ranking. The encoding ranges also overlap.

Whole-worker peak RSS ranged from 54.86 to 56.56 MiB across all cases; HTTP runtime at 8 readers used 56.19–56.38 MiB. The final SQLite file set was 49,152 bytes in every persisted sample. Runtime SQLite producer intervals were 72.8 / 75.7 / 78.8 ms versus 6.1 / 5.7 / 5.8 ms for the volatile path. This is the measured end-to-end cost of the chosen archive adapter and local filesystem, including its connection lifecycle.

At 8 HTTP readers, runtime delivered 19,953–22,447 business frames/s in aggregate; unique producer throughput was 2,494–2,806 events/s. These are bounded-burst observations for the explicitly configured reference host. They are not a supported connection maximum or an ASGI framework comparison.

"""Bounded synthetic benchmarks, separate from the installed library.

Fresh subprocesses isolate samples. Raw and core share encoding and storage;
HTTP uses the demo adapters with a benchmark-owned producer and bounded readers.
"""

import argparse
import ast
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import resource
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path
from uuid import UUID

from demos.sqlite.app import DemoHTTPServer
from demos.sqlite.channel import MemoryChannel
from demos.sqlite.constants import EventKind, JobStatus, Scenario
from demos.sqlite.events import json_bytes, sse_frame
from demos.sqlite.storage import SQLiteRepository
from ui_stream import StreamPolicy, StreamRuntime
from ui_stream.types import Binding, Checkpoint, EncodingTarget, Integrity

try:
    from sse_starlette import ServerSentEvent
except ImportError:
    ServerSentEvent = None
try:
    from ui_stream.codec import Event
    from ui_stream.sse import encode_sse_event
except ImportError:
    Event = None
    encode_sse_event = None

CHECKPOINT_EVENTS = 16
CASE_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Config:
    events: int = 512
    http_events: int = 64
    connections: tuple[int, ...] = (1, 4, 8)
    repeats: int = 3
    payload_bytes: int = 128

    def __post_init__(self):
        if not 1 <= self.events <= 1000 or not 1 <= self.http_events <= 256:
            raise ValueError("Event bounds: core 1..1000; HTTP 1..256")
        if not self.connections or len(self.connections) > 3 or any(not 1 <= item <= 8 for item in self.connections):
            raise ValueError("Use at most three connection levels in 1..8")
        if 1 not in self.connections or not 1 <= self.repeats <= 3 or not 1 <= self.payload_bytes <= 4096:
            raise ValueError("Include one connection; repeats 1..3; payload 1..4096 bytes")


@dataclass(frozen=True, slots=True)
class BenchEvent:
    kind: str
    step: int = 0
    total: int = 0
    message: str = ""
    status: str | None = None
    cursor: int | None = None
    sent_ns: int = 0

    def as_dict(self, *, include_cursor=True):
        value = {
            "kind": self.kind,
            "step": self.step,
            "total": self.total,
            "message": self.message,
            "status": self.status,
            "sent_ns": self.sent_ns,
        }
        if include_cursor:
            value["cursor"] = self.cursor
        return value


class BenchCodec:
    def business(self, data):
        if not isinstance(data, BenchEvent) or data.kind != EventKind.PROGRESS:
            raise ValueError("Expected a synthetic progress event")
        return data

    def terminal(self, status):
        return BenchEvent(kind=EventKind.FINISHED, status=status)

    def encode(self, event, target):
        return json_bytes(event.as_dict(include_cursor=target == EncodingTarget.ARCHIVE))


class DigestPublisher:
    """Consume a frame synchronously; retain a checksum instead of unbounded history."""

    def __init__(self):
        self.count = self.bytes = 0
        self.digest = hashlib.sha256()

    def publish(self, event):
        self.count += 1
        event = replace(event, cursor=self.count)
        frame = sse_frame(EventKind(event.kind), event.as_dict(), cursor=event.cursor)
        self.digest.update(frame)
        self.bytes += len(frame)
        return event


class ProbeStore:
    """Equal latest-snapshot contract with SQLite transactions or volatile state."""

    def __init__(self, repository=None):
        self.repository = repository
        self.checkpoint_batches = []
        self.commits = self.business_events = 0
        self.latest = self.completion = None

    def _accept(self, events):
        self.business_events += len(events)
        if events:
            self.latest = events[-1]

    def checkpoint(self, *, binding, events, archive_status):
        self._accept(events)
        self.checkpoint_batches.append(len(events))
        if self.repository:
            return self.repository.checkpoint(binding=binding, events=events, archive_status=archive_status)
        return Checkpoint(archive_status)

    def touch_activity(self, *, binding):
        if self.repository:
            self.repository.touch_activity(binding=binding)

    def commit(self, *, binding, events, terminal_event, archive_status, completion):
        self._accept(events)
        self.commits += 1
        self.completion = completion
        if self.repository:
            return self.repository.commit(
                binding=binding,
                events=events,
                terminal_event=terminal_event,
                archive_status=archive_status,
                completion=completion,
            )
        return archive_status


class RawLoop:
    """Equal I/O baseline; omits policy checks, locking and recovery machinery."""

    def __init__(self, *, binding, codec, publisher, archive_store):
        self.binding, self.codec, self.publisher, self.store = binding, codec, publisher, archive_store
        self.pending = []

    def send(self, data):
        event = self.codec.business(data)
        self.codec.encode(event, EncodingTarget.LIVE)
        event = self.publisher.publish(event)
        self.codec.encode(event, EncodingTarget.ARCHIVE)
        self.pending.append(event)
        if len(self.pending) == CHECKPOINT_EVENTS:
            self.store.checkpoint(binding=self.binding, events=tuple(self.pending), archive_status=Integrity.COMPLETE)
            self.pending.clear()
        return event

    def finish(self, *, status, completion):
        terminal = self.codec.terminal(status)
        self.codec.encode(terminal, EncodingTarget.ARCHIVE)
        self.codec.encode(terminal, EncodingTarget.LIVE)
        self.store.commit(
            binding=self.binding,
            events=tuple(self.pending),
            terminal_event=terminal,
            archive_status=Integrity.COMPLETE,
            completion=completion,
        )
        self.pending.clear()
        self.publisher.publish(terminal)


def make_producer(implementation, binding, publisher, store):
    options = dict(binding=binding, codec=BenchCodec(), publisher=publisher, archive_store=store)
    if implementation == "runtime":
        return StreamRuntime(**options, policy=StreamPolicy(checkpoint_event_count=CHECKPOINT_EVENTS))
    if implementation == "raw":
        return RawLoop(**options)
    raise ValueError("Unknown implementation")


def read_frames(response):
    """Parse complete SSE frames, rejecting partial and oversized synthetic output."""
    frame = {}
    while line := response.readline(16385):
        if len(line) > 16384:
            raise ValueError("Oversized SSE line")
        if line in (b"\n", b"\r\n"):
            if "data" in frame:
                frame["data"] = json.loads(frame["data"])
                yield frame
            frame = {}
        elif not line.startswith(b":"):
            name, separator, value = line.decode().strip("\r\n").partition(":")
            if separator:
                frame[name] = value.lstrip(" ")
    if frame:
        raise ValueError("Incomplete SSE frame")


def native_frame(data):
    if Event is None:
        raise RuntimeError("Optional JSON profile is not installed")
    return encode_sse_event(Event(data=data, stream_id=f'{data["step"]}-0'))


def starlette_frame(data):
    if ServerSentEvent is None:
        raise RuntimeError("Optional sse-starlette is not installed")
    return ServerSentEvent(data=json_bytes(data).decode(), id=f'{data["step"]}-0', sep="\n").encode()


def _binding(repository):
    if repository:
        job, _created = repository.create(request_token=str(UUID(int=1)), scenario=Scenario.NORMAL)
        return Binding(resource_id=job["id"], task_id=job["id"], execution_id=UUID(job["execution_id"]))
    return Binding(resource_id="synthetic", task_id="synthetic", execution_id=UUID(int=1))


def _produce(config, producer, events, *, timestamp=False):
    samples = []
    started = time.perf_counter_ns()
    for step in range(1, events + 1):
        event = BenchEvent(
            kind=EventKind.PROGRESS,
            step=step,
            total=events,
            message="x" * config.payload_bytes,
            sent_ns=time.perf_counter_ns() if timestamp else 0,
        )
        before = time.perf_counter_ns()
        producer.send(event)
        samples.append(time.perf_counter_ns() - before)
    before_commit = time.perf_counter_ns()
    producer.finish(status=JobStatus.DONE, completion={"processed": events})
    finished = time.perf_counter_ns()
    return {
        "elapsed_ns": finished - started,
        "send_ns": samples,
        "finish_ns": finished - before_commit,
        "first_event_ns": [samples[0]],
    }


def _core(config, implementation, persistence, directory):
    repository = SQLiteRepository(directory / "benchmark.sqlite3") if persistence == "sqlite" else None
    store, publisher = ProbeStore(repository), DigestPublisher()
    binding = _binding(repository)
    settings = sqlite_settings(repository) if repository else None
    result = _produce(config, make_producer(implementation, binding, publisher, store), config.events)
    if settings:
        result["sqlite_settings"] = settings
    if repository:
        saved = repository.get(binding.resource_id)
        assert saved["status"] == JobStatus.DONE and saved["result"] == {"processed": config.events}
        assert saved["snapshot"]["step"] == config.events
    result["verification"] = {
        "business_events": store.business_events,
        "published_events": publisher.count,
        "commits": store.commits,
        "checkpoint_batches": store.checkpoint_batches,
        "frame_sha256": publisher.digest.hexdigest(),
        "frame_bytes": publisher.bytes,
        "latest_step": store.latest.step,
        "completion": store.completion,
    }
    return result


class HTTPFixture:
    """Real demo HTTP/SQLite adapters, one producer and N independent readers."""

    def __init__(self, repository, connections, capacity):
        self.repository, self.binding = repository, _binding(repository)
        self.live = MemoryChannel(capacity=capacity)
        self.ready, self._lock = threading.Event(), threading.Lock()
        self._connections, self._readers = connections, 0

    def get(self, job_id):
        result = self.repository.get(job_id)
        with self._lock:
            self._readers += 1
            if self._readers == self._connections:
                self.ready.set()
        return result

    def channel(self, _job_id):
        return self.live


class BenchmarkHTTPServer(DemoHTTPServer):
    """Admit the declared eight-reader burst without the demo's default backlog of five."""

    request_queue_size = 8


def _http(config, implementation, connections, directory):
    fixture = HTTPFixture(SQLiteRepository(directory / "benchmark.sqlite3"), connections, config.http_events + 1)
    server = BenchmarkHTTPServer(fixture, 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    store = ProbeStore(fixture.repository)
    settings = sqlite_settings(fixture.repository)
    producer = make_producer(implementation, fixture.binding, fixture.live, store)

    def consume():
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        started = time.perf_counter_ns()
        first, delivery, steps, terminal = None, [], [], 0
        try:
            connection.request("GET", f"/api/jobs/{fixture.binding.resource_id}/events")
            response = connection.getresponse()
            assert response.status == 200 and response.getheader("Content-Type").startswith("text/event-stream")
            for frame in read_frames(response):
                now = time.perf_counter_ns()
                if frame["event"] == EventKind.PROGRESS:
                    first = now - started if first is None else first
                    steps.append(frame["data"]["step"])
                    delivery.append(now - frame["data"]["sent_ns"])
                elif frame["event"] == EventKind.FINISHED:
                    terminal += 1
                else:
                    raise ValueError("Unexpected control frame in no-loss run")
            assert steps == list(range(1, config.http_events + 1)) and terminal == 1
            return {"first": first, "delivery": delivery, "started": started, "finished": time.perf_counter_ns()}
        finally:
            connection.close()

    try:
        with ThreadPoolExecutor(max_workers=connections) as clients:
            futures = [clients.submit(consume) for _ in range(connections)]
            try:
                if not fixture.ready.wait(10):
                    raise TimeoutError("Local readers did not become ready")
                result = _produce(config, producer, config.http_events, timestamp=True)
                result["sqlite_settings"] = settings
            finally:
                # Release waiting readers before the executor joins on an admission/producer error.
                fixture.live.close()
            received = [future.result(timeout=10) for future in futures]
        saved = fixture.repository.get(fixture.binding.resource_id)
        assert saved["snapshot"]["step"] == config.http_events and saved["status"] == JobStatus.DONE
        result.update(
            first_event_ns=[item["first"] for item in received],
            delivery_ns=[sample for item in received for sample in item["delivery"]],
            elapsed_ns=max(item["finished"] for item in received) - min(item["started"] for item in received),
        )
        result["verification"] = {
            "received_business_events": config.http_events * connections,
            "received_terminal_events": connections,
            "status": saved["status"],
            "completion": saved["result"],
            "checkpoint_batches": store.checkpoint_batches,
        }
        return result
    finally:
        fixture.live.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def sqlite_settings(repository):
    with repository.connection() as db:
        return {
            "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0],
            "synchronous": db.execute("PRAGMA synchronous").fetchone()[0],
            "checkpoint_events": CHECKPOINT_EVENTS,
        }


def execute_case(config, layer, implementation, persistence, connections):
    if layer not in ("core", "http", "encoding") or persistence not in ("none", "sqlite"):
        raise ValueError("Invalid case")
    if connections not in config.connections:
        raise ValueError("Connection count not permitted by configuration")
    if layer in ("core", "encoding") and connections != 1:
        raise ValueError("Core and encoding cases have exactly one producer/sink")
    if layer == "encoding" and persistence != "none":
        raise ValueError("Encoding-only cases do not persist")
    with tempfile.TemporaryDirectory(prefix="ui-stream-bench-") as temporary:
        directory = Path(temporary)
        if layer == "core":
            result = _core(config, implementation, persistence, directory)
        elif layer == "http":
            if persistence != "sqlite":
                raise ValueError("HTTP fixture requires SQLite")
            result = _http(config, implementation, connections, directory)
        else:
            encoder = {"native": native_frame, "sse-starlette": starlette_frame}[implementation]
            samples, digest = [], hashlib.sha256()
            started = time.perf_counter_ns()
            for step in range(1, config.events + 1):
                before = time.perf_counter_ns()
                digest.update(encoder({"step": step, "payload": "x" * config.payload_bytes}))
                samples.append(time.perf_counter_ns() - before)
            result = {
                "elapsed_ns": time.perf_counter_ns() - started,
                "send_ns": samples,
                "first_event_ns": [samples[0]],
                "verification": {"frame_sha256": digest.hexdigest()},
            }
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["peak_rss_bytes"] = int(rss if sys.platform == "darwin" else rss * 1024)
        result["database_bytes"] = sum(path.stat().st_size for path in directory.glob("benchmark.sqlite3*"))
        result["worker_limits"] = {
            "cpu_seconds": resource.getrlimit(resource.RLIMIT_CPU)[0],
            "file_descriptors": resource.getrlimit(resource.RLIMIT_NOFILE)[0],
            "address_space_bytes": resource.getrlimit(resource.RLIMIT_AS)[0]
            if sys.platform.startswith("linux")
            else None,
            "malloc_arena_max": os.environ.get("MALLOC_ARENA_MAX"),
        }
    return result


def environment():
    def sysctl(name):
        return subprocess.check_output(["sysctl", "-n", name], text=True).strip()

    if sys.platform == "darwin":
        cpu, memory = sysctl("machdep.cpu.brand_string"), int(sysctl("hw.memsize"))
    else:
        cpu = platform.processor() or platform.machine()
        memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    versions = {}
    for package in ("ui-stream", "pydantic", "sse-starlette", "starlette", "uvicorn", "django-eventstream"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "os": platform.system(),
        "os_version": platform.mac_ver()[0] if sys.platform == "darwin" else platform.release(),
        "architecture": platform.machine(),
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "memory_bytes": memory,
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "versions": versions,
    }


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def summarize(result, events, connections):
    value = {
        "producer_events_per_second": events * 1e9 / result["elapsed_ns"],
        "delivered_events_per_second": events * connections * 1e9 / result["elapsed_ns"],
        "peak_rss_bytes": result["peak_rss_bytes"],
        "database_bytes": result["database_bytes"],
    }
    for field in ("send_ns", "first_event_ns", "delivery_ns"):
        if field in result:
            value[field] = {
                name: percentile(result[field], fraction)
                for name, fraction in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))
            }
    return value


def constrain_worker():
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(128, hard), hard))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))


def worker_environment():
    """Bound glibc arena reservations before the child interpreter initializes malloc."""
    environment = os.environ.copy()
    if sys.platform.startswith("linux"):
        environment["MALLOC_ARENA_MAX"] = "2"
    return environment


def run_worker(specification):
    """Launch every measured worker with the same allocator policy and wall deadline."""
    return subprocess.run(
        [sys.executable, "-m", "benchmarks.run", "--worker", json.dumps(specification)],
        capture_output=True,
        text=True,
        timeout=CASE_TIMEOUT_SECONDS,
        check=False,
        env=worker_environment(),
    )


def run_suite(config):
    cases = [
        ("core", implementation, persistence, 1)
        for persistence in ("none", "sqlite")
        for implementation in ("raw", "runtime")
    ]
    if Event is not None:
        cases.append(("encoding", "native", "none", 1))
    if Event is not None and ServerSentEvent is not None:
        cases.append(("encoding", "sse-starlette", "none", 1))
    cases += [
        ("http", implementation, "sqlite", count)
        for count in config.connections
        for implementation in ("raw", "runtime")
    ]
    results, started = [], time.monotonic()
    for repeat in range(config.repeats):
        for layer, implementation, persistence, connections in cases if repeat % 2 == 0 else reversed(cases):
            if time.monotonic() - started > 120:
                raise TimeoutError("Suite exceeded its 120 second scheduling budget")
            specification = {"config": asdict(config), "case": [layer, implementation, persistence, connections]}
            process = run_worker(specification)
            if process.returncode:
                raise RuntimeError(
                    f"Worker failed: {layer}/{implementation}/{persistence}/{connections}; no partial report written"
                )
            raw = json.loads(process.stdout)
            events = config.http_events if layer == "http" else config.events
            results.append(
                {
                    "repeat": repeat + 1,
                    "layer": layer,
                    "implementation": implementation,
                    "persistence": persistence,
                    "connections": connections,
                    "events": events,
                    "summary": summarize(raw, events, connections),
                    "raw": raw,
                }
            )
    sources = sorted(
        [Path("benchmarks/run.py"), *Path("src/ui_stream").glob("*.py"), *Path("demos/sqlite").glob("*.py")]
    )
    source_hashes = {
        str(path): hashlib.sha256(ast.dump(ast.parse(path.read_text())).encode()).hexdigest() for path in sources
    }
    fingerprint = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode())
    return {
        "schema_version": 1,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "invocation": "python -m benchmarks.run" + (" --smoke" if config.events == 16 else ""),
        "source_hash_method": "SHA256 of AST dumps; ignores formatting and comments",
        "source_hashes": source_hashes,
        "workload": asdict(config),
        "environment": environment(),
        "source_sha256": fingerprint.hexdigest(),
        "clock": "perf_counter_ns; one process per sample",
        "limits": {
            "core_events": 1000,
            "http_events": 256,
            "connections": 8,
            "repeats": 3,
            "worker_wall_seconds": CASE_TIMEOUT_SECONDS,
            "worker_cpu_seconds": 15,
            "worker_file_descriptors": 128,
            "linux_address_space_bytes": 512 * 1024 * 1024,
        },
        "optional_adapters": {
            "sse-starlette": "measured encoding only" if ServerSentEvent and Event else "not installed; not measured",
            "django-eventstream": "not measured: needs a separately configured Django deployment",
        },
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.worker:
        constrain_worker()
        specification = json.loads(options.worker)
        print(json.dumps(execute_case(Config(**specification["config"]), *specification["case"])))
        return
    config = Config(events=16, http_events=16, connections=(1,), repeats=2) if options.smoke else Config()
    report = run_suite(config)
    encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(encoded)
        print(f'Wrote {len(report["results"])} synthetic samples')
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()

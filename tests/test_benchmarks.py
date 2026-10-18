"""Behavior checks for the bounded, synthetic benchmark harness."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from benchmarks import run as benchmark


class BenchmarkTests(unittest.TestCase):
    def test_linux_worker_allocator_is_set_before_startup_without_relaxing_limits(self):
        self.assertTrue(callable(getattr(benchmark, "run_worker", None)), "Benchmark workers need a shared launcher")
        expected_address_space = 512 * 1024 * 1024 if sys.platform.startswith("linux") else None
        specification = {
            "config": {"events": 16, "http_events": 16, "connections": [1], "repeats": 1},
            "case": ["core", "raw", "none", 1],
        }
        with patch.dict(os.environ, {"MALLOC_ARENA_MAX": "99", "UI_STREAM_BENCHMARK_SENTINEL": "synthetic"}):
            with patch.object(benchmark.sys, "platform", "linux"):
                environment = benchmark.worker_environment()
                self.assertEqual(environment["UI_STREAM_BENCHMARK_SENTINEL"], "synthetic")
                process = benchmark.run_worker(specification)
            self.assertEqual(os.environ["MALLOC_ARENA_MAX"], "99")
            with patch.object(benchmark.sys, "platform", "darwin"):
                self.assertEqual(benchmark.worker_environment()["MALLOC_ARENA_MAX"], "99")
        self.assertEqual(process.returncode, 0, process.stderr)
        limits = json.loads(process.stdout)["worker_limits"]
        self.assertEqual(limits["malloc_arena_max"], "2")
        self.assertEqual(limits["cpu_seconds"], 15)
        self.assertEqual(limits["file_descriptors"], 128)
        self.assertEqual(limits["address_space_bytes"], expected_address_space)

    def test_rejects_unbounded_workloads(self):
        for options in (
            {"events": 1001},
            {"http_events": 257},
            {"connections": (9,)},
            {"repeats": 4},
            {"payload_bytes": 4097},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                benchmark.Config(**options)

    def test_core_and_raw_preserve_same_delivery_and_storage_contract(self):
        config = benchmark.Config(events=16, http_events=16, connections=(1,), repeats=2)
        for persistence in ("none", "sqlite"):
            results = [
                benchmark.execute_case(config, "core", implementation, persistence, 1)
                for implementation in ("raw", "runtime")
            ]
            with self.subTest(persistence=persistence):
                self.assertEqual(results[0]["verification"], results[1]["verification"])
                self.assertEqual(results[0]["verification"]["business_events"], 16)
                self.assertEqual(results[0]["verification"]["published_events"], 17)
                self.assertEqual(results[0]["verification"]["commits"], 1)
                self.assertEqual(results[0]["verification"]["checkpoint_batches"], [16])
                if persistence == "sqlite":
                    self.assertEqual(results[0]["sqlite_settings"]["journal_mode"], "wal")
                    self.assertIn(results[0]["sqlite_settings"]["synchronous"], (1, 2, 3))
                    self.assertEqual(results[0]["sqlite_settings"]["checkpoint_events"], 16)
                    self.assertEqual(results[0]["sqlite_settings"], results[1]["sqlite_settings"])
                for result in results:
                    self.assertEqual(len(result["send_ns"]), 16)
                    self.assertEqual(len(result["first_event_ns"]), 1)
                    self.assertGreater(result["elapsed_ns"], 0)
                    self.assertGreater(result["peak_rss_bytes"], 0)

    def test_http_receives_ordered_frames_and_committed_result(self):
        config = {"events": 16, "http_events": 16, "connections": [1, 4, 8], "repeats": 2}
        for connections in (1, 4, 8):
            for implementation in ("raw", "runtime"):
                specification = {"config": config, "case": ["http", implementation, "sqlite", connections]}
                with self.subTest(connections=connections, implementation=implementation):
                    process = benchmark.run_worker(specification)
                    self.assertEqual(process.returncode, 0, process.stderr)
                    result = json.loads(process.stdout)
                    self.assertEqual(result["verification"]["received_business_events"], 16 * connections)
                    self.assertEqual(result["verification"]["received_terminal_events"], connections)
                    self.assertEqual(result["verification"]["status"], "DONE")
                    self.assertEqual(result["verification"]["completion"], {"processed": 16})
                    self.assertEqual(len(result["delivery_ns"]), 16 * connections)
                    self.assertEqual(len(result["first_event_ns"]), connections)

    def test_failed_admission_releases_readers_before_executor_shutdown(self):
        script = """
from benchmarks import run as b
class RefusingFixture(b.HTTPFixture):
    def __init__(self, *args):
        super().__init__(*args)
        self.ready.wait = lambda timeout: False
b.HTTPFixture = RefusingFixture
try:
    b.execute_case(b.Config(events=16, http_events=16), "http", "raw", "sqlite", 1)
except TimeoutError:
    print("admission rejected cleanly")
"""
        process = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=benchmark.CASE_TIMEOUT_SECONDS,
            env=benchmark.worker_environment(),
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "admission rejected cleanly")

    def test_sse_parser_ignores_comments_and_requires_complete_frame(self):
        frame = b': heartbeat\n\nevent: progress\nid: 1\ndata: {"step":1}\n\n'
        self.assertEqual(
            list(benchmark.read_frames(io.BytesIO(frame))), [{"event": "progress", "id": "1", "data": {"step": 1}}]
        )
        with self.assertRaises(ValueError):
            list(benchmark.read_frames(io.BytesIO(b'event: progress\ndata: {}\n')))

    def test_optional_encoder_adapter_produces_identical_sse_bytes(self):
        if importlib.util.find_spec("sse_starlette") is None or benchmark.Event is None:
            self.skipTest("Optional installed adapter unavailable")
        for step in (1, 16):
            data = {"step": step, "payload": "x" * 128}
            self.assertEqual(benchmark.native_frame(data), benchmark.starlette_frame(data))

    def test_case_contract_rejects_mislabeled_or_unbounded_output(self):
        config = benchmark.Config(connections=(1, 4))
        for case in (("core", "runtime", "none", 4), ("encoding", "native", "sqlite", 1), ("http", "raw", "none", 1)):
            with self.subTest(case=case), self.assertRaises(ValueError):
                benchmark.execute_case(config, *case)

    def test_percentiles_use_nearest_rank_without_averaging(self):
        self.assertEqual(benchmark.percentile([4, 1, 3, 2], 0.50), 2)
        self.assertEqual(benchmark.percentile([4, 1, 3, 2], 0.95), 4)
        self.assertEqual(benchmark.percentile([4, 1, 3, 2], 0.99), 4)

    def test_public_metadata_has_no_host_or_local_paths(self):
        metadata = benchmark.environment()
        self.assertTrue(
            {"os", "os_version", "architecture", "cpu", "logical_cpus", "memory_bytes", "python", "versions"}
            <= metadata.keys()
        )
        serialized = json.dumps(metadata)
        for forbidden in ("/Users" + "/", "/home" + "/", "hostname", "username", "executable"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()

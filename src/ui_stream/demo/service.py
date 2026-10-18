"""Single-process demo lifecycle and synthetic tasks assembled from public component ports.

Creation is idempotent under one service lock. Shutdown/restart interrupts work;
there is no task queue or automatic resumption. Final commit retries reuse the
same open runtime rather than repeating the synthetic computation.
"""

import logging
import threading
from pathlib import Path
from uuid import UUID

from ui_stream import StreamPolicy, StreamRuntime
from ui_stream.demo.channel import DemoPublisher, MemoryChannel
from ui_stream.demo.constants import MAX_ACTIVE_JOBS, MAX_CHANNELS, TOTAL_STEPS, EventKind, JobStatus, Scenario
from ui_stream.demo.events import DemoCodec, DemoEvent
from ui_stream.demo.storage import DatabaseLease, SQLiteArchiveStore, SQLiteRepository
from ui_stream.exceptions import ArchiveUnavailable, StaleExecution
from ui_stream.types import Binding

logger = logging.getLogger(__name__)


class DemoBusy(RuntimeError):
    pass


class DemoService:
    """Own one local database lease, bounded producers and the in-memory live channels."""

    def __init__(self, database: Path, *, interval: float = 0.5, retry_delay: float = 1):
        self._lease = DatabaseLease(Path(database).resolve())
        try:
            self.repository = SQLiteRepository(database)
            self.repository.interrupt_running()
        except Exception:
            self._lease.close()
            raise
        self._interval = interval
        self._retry_delay = retry_delay
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._channels: dict[str, MemoryChannel] = {}
        self._threads: dict[str, threading.Thread] = {}

    def create(self, *, request_token: str, scenario: Scenario) -> tuple[dict, bool]:
        scenario = Scenario(scenario)
        with self._lock:
            existing = self.repository.by_token(request_token)
            if existing is not None:
                if existing["scenario"] != scenario:
                    raise ValueError("A request token cannot change scenario")
                return self.repository.get(existing["id"]), False
            if self._stop.is_set() or sum(thread.is_alive() for thread in self._threads.values()) >= MAX_ACTIVE_JOBS:
                raise DemoBusy("Four demo jobs are already running; wait for one to finish")
            job, created = self.repository.create(request_token=request_token, scenario=scenario)
            channel = MemoryChannel()
            self._channels[job["id"]] = channel
            thread = threading.Thread(target=self._run, args=(job, channel), name="ui-stream-demo", daemon=True)
            self._threads[job["id"]] = thread
            thread.start()
            for job_id in list(self._channels):
                if len(self._channels) <= MAX_CHANNELS:
                    break
                if not self._threads[job_id].is_alive():
                    self._threads.pop(job_id)
                    self._channels.pop(job_id)
            return job, created

    def get(self, job_id: str) -> dict:
        return self.repository.get(job_id)

    def channel(self, job_id: str) -> MemoryChannel | None:
        with self._lock:
            return self._channels.get(job_id)

    def _run(self, job: dict, channel: MemoryChannel) -> None:
        binding = Binding(resource_id=job["id"], task_id=job["id"], execution_id=UUID(job["execution_id"]))
        scenario = Scenario(job["scenario"])
        runtime = StreamRuntime(
            binding=binding,
            publisher=DemoPublisher(channel, scenario),
            codec=DemoCodec(),
            archive_store=SQLiteArchiveStore(self.repository, scenario=scenario),
            policy=StreamPolicy(checkpoint_event_count=3),
        )
        try:
            status = JobStatus.DONE
            completion = {"processed": TOTAL_STEPS, "summary": "12 synthetic items completed"}
            for step in range(1, TOTAL_STEPS + 1):
                if self._stop.wait(self._interval):
                    runtime.finish_retry()
                    self.repository.interrupt(binding=binding, warning="The demo stopped; this job will not resume")
                    return
                runtime.send(DemoEvent(kind=EventKind.PROGRESS, step=step, message=f"Synthetic item {step}"))
                if scenario == Scenario.BUSINESS_ERROR and step == 6:
                    status = JobStatus.ERROR
                    completion = {"error": "Synthetic task failure at step 6"}
                    break
            for attempt in range(2):
                try:
                    runtime.finish(status=status, completion=completion)
                    break
                except ArchiveUnavailable:
                    if scenario != Scenario.COMMIT_FAILURE or attempt:
                        raise
                    if self._stop.wait(self._retry_delay):
                        runtime.finish_retry()
                        self.repository.interrupt(binding=binding, warning="The demo stopped before the commit retry")
                        return
        except StaleExecution:
            logger.warning("Demo writer lost execution ownership")
        except Exception:
            logger.exception("Demo job could not settle")
            # Host cleanup never invents a successful result after a failed transaction.
            try:
                self.repository.interrupt(binding=binding, warning="The demo could not finish; inspect the local log")
            except Exception:
                logger.exception("Demo interruption could not be persisted")
        finally:
            channel.close()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=5)
        for channel in self._channels.values():
            channel.close()
        self._lease.close()

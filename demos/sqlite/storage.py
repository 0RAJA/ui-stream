"""SQLite latest-value snapshots and atomic result transactions for the demo.

Each operation owns a connection and a real short transaction; no ambient ORM
transaction exists. A process lease prevents a second host interrupting live jobs.
"""

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from demos.sqlite.constants import RECOVERABLE_SQLITE_ERRORS, JobStatus, Scenario
from demos.sqlite.events import DemoCodec, DemoEvent, json_bytes
from ui_stream.archive import merge_archive_status
from ui_stream.exceptions import ArchiveUnavailable, StaleExecution
from ui_stream.types import Checkpoint, ExecutionBinding, Integrity


class DatabaseInUse(RuntimeError):
    pass


class DatabaseLease:
    """Hold a non-blocking process lock for one demo database until service shutdown."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.with_suffix(path.suffix + ".lock").open("a+")
        try:
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._file.close()
            raise DatabaseInUse("This SQLite database already has a running demo") from None

    def close(self):
        self._file.close()


@contextmanager
def translate_archive_errors():
    try:
        yield
    except sqlite3.OperationalError as error:
        if getattr(error, "sqlite_errorcode", 0) & 0xFF in RECOVERABLE_SQLITE_ERRORS:
            raise ArchiveUnavailable("Demo SQLite storage unavailable") from None
        # SQL mistakes are implementation errors, not recoverable infrastructure outages.
        raise


class SQLiteRepository:
    """Own demo jobs, fencing and the transaction containing snapshot, terminal and result."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS ui_stream_demo_jobs (
                id TEXT PRIMARY KEY, request_token TEXT UNIQUE NOT NULL,
                execution_id TEXT NOT NULL, scenario TEXT NOT NULL, status TEXT NOT NULL,
                snapshot_json TEXT, cursor INTEGER NOT NULL DEFAULT 0,
                terminal_json TEXT, result_json TEXT, integrity TEXT NOT NULL,
                warning TEXT NOT NULL DEFAULT ''
            )"""
            )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def _decode(row):
        if row is None:
            raise KeyError("Unknown demo job")
        value = dict(row)
        value.pop("request_token")
        for field, name in (("snapshot_json", "snapshot"), ("terminal_json", "terminal"), ("result_json", "result")):
            raw = value.pop(field)
            value[name] = json.loads(raw) if raw is not None else None
        return value

    def create(self, *, request_token: str, scenario: Scenario) -> tuple[dict, bool]:
        with self.transaction() as db:
            existing = db.execute(
                "SELECT * FROM ui_stream_demo_jobs WHERE request_token=?", (request_token,)
            ).fetchone()
            if existing:
                if existing["scenario"] != scenario:
                    raise ValueError("A request token cannot change scenario")
                return self._decode(existing), False
            job_id, execution_id = str(uuid4()), str(uuid4())
            db.execute(
                "INSERT INTO ui_stream_demo_jobs (id,request_token,execution_id,scenario,status,integrity) "
                "VALUES (?,?,?,?,?,?)",
                (job_id, request_token, execution_id, scenario, JobStatus.RUNNING, Integrity.COMPLETE),
            )
            value = self._decode(db.execute("SELECT * FROM ui_stream_demo_jobs WHERE id=?", (job_id,)).fetchone())
        return value, True

    def by_token(self, request_token):
        with self.connection() as db:
            row = db.execute("SELECT * FROM ui_stream_demo_jobs WHERE request_token=?", (request_token,)).fetchone()
        return dict(row) if row else None

    def get(self, job_id: str) -> dict:
        with self.connection() as db:
            return self._decode(db.execute("SELECT * FROM ui_stream_demo_jobs WHERE id=?", (job_id,)).fetchone())

    @staticmethod
    def _require_current(db, binding):
        row = db.execute("SELECT * FROM ui_stream_demo_jobs WHERE id=?", (binding.resource_id,)).fetchone()
        if (
            row is None
            or binding.task_id != row["id"]
            or str(binding.execution_id) != row["execution_id"]
            or row["status"] != JobStatus.RUNNING
        ):
            raise StaleExecution("Demo execution is no longer current")
        return row

    @staticmethod
    def _snapshot(row, events):
        snapshot = row["snapshot_json"]
        cursor = row["cursor"]
        for event in events:
            snapshot = json_bytes(event.as_dict()).decode()
            if event.cursor is not None:
                cursor = max(cursor, event.cursor)
        return snapshot, cursor

    def checkpoint(self, *, binding, events, archive_status):
        with self.transaction() as db:
            row = self._require_current(db, binding)
            snapshot, cursor = self._snapshot(row, events)
            integrity = merge_archive_status(row["integrity"], archive_status)
            db.execute(
                "UPDATE ui_stream_demo_jobs SET snapshot_json=?,cursor=?,integrity=? WHERE id=?",
                (snapshot, cursor, integrity, row["id"]),
            )
        return Checkpoint(archive_status=integrity)

    def touch_activity(self, *, binding):
        with self.transaction() as db:
            self._require_current(db, binding)

    def commit(self, *, binding, events, terminal_event, archive_status, completion):
        with self.transaction() as db:
            row = self._require_current(db, binding)
            snapshot, cursor = self._snapshot(row, events)
            integrity = merge_archive_status(row["integrity"], archive_status)
            db.execute(
                "UPDATE ui_stream_demo_jobs SET snapshot_json=?,cursor=?,integrity=?,status=?,"
                "terminal_json=?,result_json=? WHERE id=?",
                (
                    snapshot,
                    cursor,
                    integrity,
                    terminal_event.status,
                    json_bytes(terminal_event.as_dict()).decode(),
                    json_bytes(completion).decode(),
                    row["id"],
                ),
            )
        return integrity

    def note_issue(self, *, binding, warning):
        with self.transaction() as db:
            self._require_current(db, binding)
            db.execute("UPDATE ui_stream_demo_jobs SET warning=? WHERE id=?", (warning, binding.resource_id))

    def interrupt(self, *, binding, warning):
        with self.transaction() as db:
            row = self._require_current(db, binding)
            integrity = merge_archive_status(row["integrity"], Integrity.DEGRADED)
            terminal = DemoCodec().terminal(JobStatus.INTERRUPTED)
            db.execute(
                "UPDATE ui_stream_demo_jobs SET status=?,integrity=?,terminal_json=?,warning=? WHERE id=?",
                (JobStatus.INTERRUPTED, integrity, json_bytes(terminal.as_dict()).decode(), warning, row["id"]),
            )

    def interrupt_running(self):
        with self.transaction() as db:
            terminal = DemoCodec().terminal(JobStatus.INTERRUPTED)
            db.execute(
                "UPDATE ui_stream_demo_jobs SET status=?,integrity=CASE WHEN integrity=? THEN ? ELSE ? END,"
                "terminal_json=?,warning=? WHERE status=?",
                (
                    JobStatus.INTERRUPTED,
                    Integrity.TRUNCATED,
                    Integrity.TRUNCATED,
                    Integrity.DEGRADED,
                    json_bytes(terminal.as_dict()).decode(),
                    "The previous process stopped; this job will not resume automatically",
                    JobStatus.RUNNING,
                ),
            )


class SQLiteArchiveStore:
    """Adapt SQLite transactions and declared one-shot storage faults to the public port."""

    def __init__(self, repository: SQLiteRepository, *, scenario: Scenario):
        self._repository = repository
        self._scenario = scenario
        self._checkpoint_failed = False
        self._commit_failed = False

    def checkpoint(
        self, *, binding: ExecutionBinding, events: tuple[DemoEvent, ...], archive_status: Integrity
    ) -> Checkpoint:
        with translate_archive_errors():
            if self._scenario == Scenario.CHECKPOINT_FAILURE and not self._checkpoint_failed:
                self._checkpoint_failed = True
                self._repository.note_issue(binding=binding, warning="Checkpoint failed once; the next batch retries")
                raise ArchiveUnavailable("Synthetic checkpoint failure")
            return self._repository.checkpoint(binding=binding, events=events, archive_status=archive_status)

    def touch_activity(self, *, binding: ExecutionBinding) -> None:
        with translate_archive_errors():
            self._repository.touch_activity(binding=binding)

    def commit(
        self,
        *,
        binding: ExecutionBinding,
        events: tuple[DemoEvent, ...],
        terminal_event: DemoEvent,
        archive_status: Integrity,
        completion: dict
    ) -> Integrity:
        with translate_archive_errors():
            if self._scenario == Scenario.COMMIT_FAILURE and not self._commit_failed:
                self._commit_failed = True
                self._repository.note_issue(
                    binding=binding, warning="Final commit failed once; retrying this execution"
                )
                raise ArchiveUnavailable("Synthetic final commit failure")
            return self._repository.commit(
                binding=binding,
                events=events,
                terminal_event=terminal_event,
                archive_status=archive_status,
                completion=completion,
            )

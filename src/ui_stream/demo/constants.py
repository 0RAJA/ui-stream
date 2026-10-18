"""Domain vocabulary and bounds belonging to the local demo, not the component core."""

import sqlite3
from enum import StrEnum


class Scenario(StrEnum):
    NORMAL = "normal"
    BUSINESS_ERROR = "business_error"
    LIVE_FAILURE = "live_failure"
    CHECKPOINT_FAILURE = "checkpoint_failure"
    COMMIT_FAILURE = "commit_failure"


class JobStatus(StrEnum):
    RUNNING = "RUNNING"
    DONE = "DONE"
    ERROR = "ERROR"
    INTERRUPTED = "INTERRUPTED"


class EventKind(StrEnum):
    PROGRESS = "progress"
    FINISHED = "finished"
    STATE = "state"
    RESET = "reset"
    SYNC = "sync"


TOTAL_STEPS = 12
CHANNEL_CAPACITY = 64
MAX_ACTIVE_JOBS = 4
MAX_CHANNELS = 32
MAX_REQUEST_BYTES = 2048
RECOVERABLE_SQLITE_ERRORS = frozenset(
    (
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_FULL,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_CANTOPEN,
        sqlite3.SQLITE_READONLY,
    )
)

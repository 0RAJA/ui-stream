"""Local HTTP host for the demo page, snapshots and native SSE.

Routes are fixed and the server binds only 127.0.0.1. Readers recover from
SQLite on initial/reconnected requests; live gaps and missing terminal notices
request a snapshot refresh rather than claiming historical replay.
"""

import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from socketserver import TCPServer
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from demos.sqlite.constants import MAX_REQUEST_BYTES, EventKind, JobStatus, Scenario
from demos.sqlite.events import DemoSSEMessage, encode_sse_message, json_bytes
from demos.sqlite.service import DemoBusy, DemoService
from ui_stream.sse import SSEBody

ASSETS = {
    "/": ("index.html", "text/html"),
    "/demo.js": ("demo.js", "text/javascript"),
    "/demo.css": ("demo.css", "text/css"),
}
JOB_ROUTE = re.compile(r"^/api/jobs/([^/]+)(/events)?$")
CURSOR_PATTERN = re.compile(r"^(0|[1-9][0-9]{0,12})$")


class DemoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service: DemoService, port: int):
        self.service = service
        super().__init__(("127.0.0.1", port), DemoHandler)

    def server_bind(self):
        # The fixed loopback host needs no reverse DNS, which may stall on offline runners.
        TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


class DemoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        # Synthetic task diagnostics are explicit; do not turn every heartbeat into a log.
        pass

    def _response(self, status, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.close_connection = True
        if self.path != "/api/jobs":
            self._response(404, {"error": "Unknown demo route"})
            return
        if self.headers.get_content_type() != "application/json":
            self._response(415, {"error": "Use application/json"})
            return
        origin = self.headers.get("Origin")
        if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
            self._response(403, {"error": "Use the local demo page"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST_BYTES:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or set(data) != {"request_token", "scenario"}:
                raise ValueError()
            token = str(UUID(data["request_token"]))
            scenario = Scenario(data["scenario"])
        except (ValueError, TypeError, AttributeError):
            self._response(400, {"error": "Expected a UUID request_token and a supported scenario"})
            return
        try:
            job, created = self.server.service.create(request_token=token, scenario=scenario)
        except ValueError:
            self._response(409, {"error": "This creation request already uses a different scenario"})
        except DemoBusy as error:
            self._response(503, {"error": str(error)})
        except sqlite3.Error:
            self._response(503, {"error": "Local SQLite storage unavailable; retry the same request"})
        else:
            self._response(201 if created else 200, job)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path in ASSETS:
            name, content_type = ASSETS[parsed.path]
            self._response(200, files("demos.sqlite").joinpath("static", name).read_bytes(), content_type)
            return
        match = JOB_ROUTE.fullmatch(parsed.path)
        if not match:
            self._response(404, {"error": "Unknown demo route"})
            return
        try:
            job_id = str(UUID(match[1]))
            query = parse_qs(parsed.query, keep_blank_values=True)
            if set(query) - {"after"} or any(len(values) != 1 for values in query.values()):
                raise ValueError()
            raw_cursor = query.get("after", [self.headers.get("Last-Event-ID", "0")])[0]
            if not CURSOR_PATTERN.fullmatch(raw_cursor):
                raise ValueError()
            cursor = int(raw_cursor)
        except (ValueError, TypeError):
            self._response(400, {"error": "Invalid job ID or cursor"})
            return
        try:
            job = self.server.service.get(job_id)
        except KeyError:
            self._response(404, {"error": "This demo job does not exist"})
            return
        except sqlite3.Error:
            self._response(503, {"error": "Local SQLite storage unavailable"})
            return
        if match[2]:
            self._stream(job, cursor)
        else:
            self._response(200, job)

    def _stream(self, job: dict, cursor: int):
        self.close_connection = True
        self.connection.settimeout(10)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        body = SSEBody(self._stream_events(job, cursor), encoder=encode_sse_message)
        try:
            for frame in body:
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        finally:
            body.close()

    def _stream_events(self, job: dict, cursor: int):
        """读取这个连接的尾流；连接关闭不关闭共享 channel 或后台任务。"""

        if job["status"] != JobStatus.RUNNING:
            yield DemoSSEMessage(EventKind.STATE, job)
            return
        channel = self.server.service.channel(job["id"])
        if channel is None:
            yield DemoSSEMessage(EventKind.SYNC, {"reason": "producer_unavailable"})
            return
        while True:
            batch = channel.read(cursor)
            if batch.gap:
                yield DemoSSEMessage(EventKind.RESET, {"reason": "live_tail_gap"})
                return
            for event in batch.events:
                yield DemoSSEMessage(EventKind(event.kind), event.as_dict(), event.cursor)
                cursor = event.cursor
                if event.kind == EventKind.FINISHED:
                    return
            if batch.closed:
                yield DemoSSEMessage(EventKind.SYNC, {"reason": "producer_closed"})
                return
            if not batch.events:
                yield None


def make_server(service: DemoService, *, port: int = 8765) -> DemoHTTPServer:
    return DemoHTTPServer(service, port)


__all__ = ["DemoService", "make_server"]

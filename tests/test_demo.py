"""Independent SQLite/HTTP demo contracts, executed without a host application import path."""

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

BOOTSTRAP = """
import faulthandler
import json
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
from unittest.mock import patch

from ui_stream import Integrity, StreamRuntime, StreamPolicy
from ui_stream.exceptions import ArchiveUnavailable, StaleExecution
from ui_stream.types import Binding
from demos.sqlite.app import DemoService, make_server
from demos.sqlite.channel import MemoryChannel
from demos.sqlite.constants import JobStatus, Scenario
from demos.sqlite.events import DemoCodec, DemoEvent
from demos.sqlite.storage import DatabaseInUse, SQLiteArchiveStore, SQLiteRepository

faulthandler.dump_traceback_later(10, exit=True)

@contextmanager
def repository():
    with tempfile.TemporaryDirectory() as folder:
        yield SQLiteRepository(Path(folder) / "demo.sqlite3")

def create(repo, scenario=Scenario.NORMAL, token=None):
    return repo.create(request_token=token or str(uuid4()), scenario=scenario)

def binding(job):
    return Binding(resource_id=job["id"], task_id=job["id"], execution_id=job["execution_id"])

def event(step, cursor=None):
    return DemoEvent(kind="progress", step=step, total=12, message=f"Synthetic item {step}", cursor=cursor)

def wait_job(service, job_id):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        job=service.get(job_id)
        if job["status"] != "RUNNING":
            return job
        time.sleep(0.01)
    raise AssertionError("demo job did not settle")

@contextmanager
def http_demo(interval=0.003):
    with tempfile.TemporaryDirectory() as folder:
        service=DemoService(Path(folder)/"demo.sqlite3", interval=interval, retry_delay=0.02)
        server=make_server(service, port=0)
        thread=threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield service, f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            service.close()
            thread.join(2)

def post(url, payload):
    request=Request(url+"/api/jobs", data=json.dumps(payload).encode(),
                    headers={"Content-Type":"application/json"}, method="POST")
    with urlopen(request, timeout=5) as response:
        return response.status, json.load(response)
"""


class StandaloneDemoTest(unittest.TestCase):
    def run_demo(self, source):
        root = os.environ.get("UI_STREAM_TEST_PATH", str(Path(__file__).resolve().parents[1] / "src"))
        script = (
            "import sys; sys.path[:0] = "
            + repr([root, str(Path(__file__).resolve().parents[1])])
            + "\n"
            + BOOTSTRAP
            + textwrap.dedent(source)
        )
        result = subprocess.run([sys.executable, "-I", "-S", "-c", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sqlite_snapshot_survives_reopening_and_commit_is_atomic(self):
        self.run_demo(
            r"""
            with repository() as repo:
                job, created = create(repo)
                assert created
                bound = binding(job)
                store = SQLiteArchiveStore(repo, scenario=Scenario.NORMAL)
                store.checkpoint(binding=bound, events=(event(3, 3),), archive_status=Integrity.COMPLETE)
                reopened = SQLiteRepository(repo.path)
                saved = reopened.get(job["id"])
                assert saved["snapshot"]["step"] == 3 and saved["cursor"] == 3
                terminal = DemoCodec().terminal("DONE")
                store.commit(binding=bound, events=(event(12, 12),), terminal_event=terminal,
                             archive_status=Integrity.COMPLETE, completion={"processed":12})
                saved = reopened.get(job["id"])
                assert saved["snapshot"]["step"] == 12 and saved["status"] == "DONE"
                assert saved["terminal"]["status"] == "DONE" and saved["result"] == {"processed":12}
            """
        )

    def test_sqlite_trigger_failure_rolls_back_tail_terminal_and_result(self):
        self.run_demo(
            r"""
            with repository() as repo:
                job, _ = create(repo)
                bound = binding(job)
                store = SQLiteArchiveStore(repo, scenario=Scenario.NORMAL)
                store.checkpoint(binding=bound, events=(event(3, 3),), archive_status=Integrity.COMPLETE)
                with sqlite3.connect(repo.path) as db:
                    db.execute("CREATE TRIGGER stop_finish BEFORE UPDATE OF status ON ui_stream_demo_jobs "
                               "WHEN NEW.status='DONE' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
                try:
                    store.commit(binding=bound, events=(event(12, 12),), terminal_event=DemoCodec().terminal("DONE"),
                                 archive_status=Integrity.COMPLETE, completion={"processed":12})
                except sqlite3.IntegrityError:
                    pass
                else:
                    raise AssertionError("SQLite trigger error swallowed")
                saved = repo.get(job["id"])
                assert saved["status"] == "RUNNING" and saved["snapshot"]["step"] == 3
                assert saved["result"] is None and saved["terminal"] is None and saved["cursor"] == 3
            """
        )

    def test_sqlite_fences_stale_execution_without_overwriting_snapshot(self):
        self.run_demo(
            r"""
            with repository() as repo:
                job, _ = create(repo)
                stale = Binding(resource_id=job["id"], task_id=job["id"], execution_id=str(uuid4()))
                store = SQLiteArchiveStore(repo, scenario=Scenario.NORMAL)
                try:
                    store.checkpoint(binding=stale, events=(event(3),), archive_status=Integrity.COMPLETE)
                except StaleExecution:
                    pass
                else:
                    raise AssertionError("stale writer accepted")
                assert repo.get(job["id"])["snapshot"] is None
            """
        )

    def test_duplicate_request_token_returns_same_job_and_rejects_changed_scenario(self):
        self.run_demo(
            r"""
            with repository() as repo:
                token=str(uuid4())
                first, created=create(repo, token=token)
                second, again=create(repo, token=token)
                assert created and not again and first["id"] == second["id"]
                try:
                    create(repo, token=token, scenario=Scenario.BUSINESS_ERROR)
                except ValueError:
                    pass
                else:
                    raise AssertionError("same token accepted different parameters")
            """
        )

    def test_channel_has_independent_readers_gap_detection_and_close(self):
        self.run_demo(
            r"""
            channel=MemoryChannel(capacity=2)
            first=channel.publish(event(1))
            channel.publish(event(2))
            assert first.cursor == 1
            assert [item.step for item in channel.read(0, timeout=0).events] == [1,2]
            assert [item.step for item in channel.read(1, timeout=0).events] == [2]
            channel.publish(event(3))
            assert channel.read(0, timeout=0).gap
            assert [item.step for item in channel.read(1, timeout=0).events] == [2,3]
            channel.close()
            assert channel.read(3, timeout=0).closed
            """
        )

    def test_checkpoint_failure_recovers_without_losing_pending(self):
        self.run_demo(
            r"""
            with repository() as repo:
                job, _=create(repo, scenario=Scenario.CHECKPOINT_FAILURE)
                channel=MemoryChannel()
                runtime=StreamRuntime(binding=binding(job), publisher=channel, codec=DemoCodec(),
                                      archive_store=SQLiteArchiveStore(repo, scenario=Scenario.CHECKPOINT_FAILURE),
                                      policy=StreamPolicy(checkpoint_event_count=3))
                for step in (1,2,3):
                    runtime.send(event(step))
                assert runtime.pending_count == 3 and repo.get(job["id"])["snapshot"] is None
                runtime.send(event(4))
                assert runtime.pending_count == 0 and repo.get(job["id"])["snapshot"]["step"] == 4
                assert runtime.archive_status == Integrity.DEGRADED
            """
        )

    def test_commit_failure_keeps_runtime_open_and_never_publishes_finished(self):
        self.run_demo(
            r"""
            with repository() as repo:
                job, _=create(repo, scenario=Scenario.COMMIT_FAILURE)
                channel=MemoryChannel()
                runtime=StreamRuntime(binding=binding(job), publisher=channel, codec=DemoCodec(),
                                      archive_store=SQLiteArchiveStore(repo, scenario=Scenario.COMMIT_FAILURE))
                runtime.send(event(1))
                try:
                    runtime.finish(status="DONE", completion={"processed":1})
                except ArchiveUnavailable:
                    pass
                else:
                    raise AssertionError("commit failure swallowed")
                assert runtime.pending_count == 1 and not runtime.closed
                assert repo.get(job["id"])["status"] == "RUNNING"
                assert all(item.kind == "progress" for item in channel.read(0, timeout=0).events)
                runtime.finish(status="DONE", completion={"processed":1})
                assert runtime.closed and repo.get(job["id"])["result"] == {"processed":1}
            """
        )

    def test_all_five_demo_scenarios_settle_with_expected_saved_result(self):
        self.run_demo(
            r"""
            with tempfile.TemporaryDirectory() as folder:
                service=DemoService(Path(folder)/"demo.sqlite3", interval=0.002, retry_delay=0.01)
                try:
                    for scenario in Scenario:
                        job, _=service.create(request_token=str(uuid4()), scenario=scenario)
                        saved=wait_job(service, job["id"])
                        expected="ERROR" if scenario == Scenario.BUSINESS_ERROR else "DONE"
                        assert saved["status"] == expected, saved
                        assert saved["terminal"]["status"] == expected
                        assert saved["snapshot"]["step"] == (6 if expected == "ERROR" else 12)
                        if expected == "DONE":
                            assert saved["result"] == {"processed":12,"summary":"12 synthetic items completed"}
                        else:
                            assert saved["result"]["error"] == "Synthetic task failure at step 6"
                        if scenario in (Scenario.LIVE_FAILURE, Scenario.CHECKPOINT_FAILURE):
                            assert saved["integrity"] == "DEGRADED"
                finally:
                    service.close()
            forbidden=("django","pydantic","redis","services","rest_framework","celery")
            assert not any(name.split(".")[0] in forbidden for name in sys.modules)
            """
        )

    def test_concurrent_duplicate_posts_start_one_job(self):
        self.run_demo(
            r"""
            from concurrent.futures import ThreadPoolExecutor
            with http_demo() as (service, url):
                payload={"request_token":str(uuid4()),"scenario":"normal"}
                with ThreadPoolExecutor(max_workers=4) as pool:
                    responses=list(pool.map(lambda _: post(url,payload),range(4)))
                assert sum(status==201 for status,_ in responses) == 1
                ids={job["id"] for _,job in responses}
                assert len(ids)==1
                saved=wait_job(service,ids.pop())
                assert saved["result"]["processed"] == 12
                assert saved["snapshot"]["step"] == 12 and saved["cursor"] == 12
            """
        )

    def test_http_stream_outputs_progress_and_terminal_before_end(self):
        self.run_demo(
            r"""
            with http_demo() as (service,url):
                _, job=post(url,{"request_token":str(uuid4()),"scenario":"normal"})
                with urlopen(url+"/api/jobs/"+job["id"]+"/events?after=0",timeout=5) as response:
                    payload=response.read().decode()
                assert "event: progress" in payload and "event: finished" in payload
                assert payload.index("event: progress") < payload.index("event: finished")
                saved=service.get(job["id"])
                assert saved["result"]["processed"] == 12
            """
        )

    def test_finished_reconnect_returns_saved_state_then_closes(self):
        self.run_demo(
            r"""
            with http_demo() as (service,url):
                _, job=post(url,{"request_token":str(uuid4()),"scenario":"normal"})
                saved=wait_job(service,job["id"])
                with urlopen(url+"/api/jobs/"+job["id"]+"/events?after=0",timeout=5) as response:
                    payload=response.read().decode()
                assert payload.startswith("event: state\n") and '"status":"DONE"' in payload
                assert "event: progress" not in payload and "event: finished" not in payload
                assert saved["snapshot"]["step"] == 12
            """
        )

    def test_disconnect_does_not_cancel_and_snapshot_recovery_replaces_progress(self):
        self.run_demo(
            r"""
            with http_demo() as (service,url):
                _,job=post(url,{"request_token":str(uuid4()),"scenario":"normal"})
                source=urlopen(url+"/api/jobs/"+job["id"]+"/events?after=0",timeout=5)
                source.readline()
                source.close()
                saved=wait_job(service,job["id"])
                with urlopen(url+"/api/jobs/"+job["id"],timeout=5) as response:
                    restored=json.load(response)
                assert restored["snapshot"]["step"] == 12 and restored["result"]["processed"] == 12
                assert not isinstance(restored["snapshot"],list)
            """
        )

    def test_invalid_payload_cursor_and_unknown_job_are_rejected(self):
        self.run_demo(
            r"""
            with http_demo() as (service,url):
                for payload in ({"request_token":"bad","scenario":"normal"},
                                {"request_token":str(uuid4()),"scenario":"unknown"},
                                {"request_token":str(uuid4()),"scenario":"normal","extra":True}):
                    try:
                        post(url,payload)
                    except HTTPError as error:
                        assert error.code == 400
                    else:
                        raise AssertionError("invalid payload accepted")
                _,job=post(url,{"request_token":str(uuid4()),"scenario":"normal"})
                for suffix,expected in (("/"+job["id"]+"/events?after=-1",400),
                                       ("/"+job["id"]+"/events?after=0&after=1",400),
                                       ("/"+str(uuid4()),404)):
                    try:
                        urlopen(url+"/api/jobs"+suffix,timeout=5)
                    except HTTPError as error:
                        assert error.code == expected
                    else:
                        raise AssertionError("invalid URL accepted")
            """
        )

    def test_restart_preserves_snapshot_and_marks_running_job_interrupted(self):
        self.run_demo(
            r"""
            with repository() as repo:
                job,_=create(repo)
                SQLiteArchiveStore(repo,scenario=Scenario.NORMAL).checkpoint(
                    binding=binding(job),events=(event(3,3),),archive_status=Integrity.COMPLETE)
                service=DemoService(repo.path,interval=0.001)
                try:
                    saved=service.get(job["id"])
                    assert saved["status"] == "INTERRUPTED" and saved["integrity"] == "DEGRADED"
                    assert saved["snapshot"]["step"] == 3 and saved["result"] is None
                    time.sleep(0.03)
                    assert service.get(job["id"])["snapshot"]["step"] == 3
                finally:
                    service.close()
            """
        )

    def test_second_demo_service_cannot_take_over_same_database(self):
        self.run_demo(
            r"""
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/"demo.sqlite3"
                first=DemoService(path)
                try:
                    try:
                        second=DemoService(path)
                    except DatabaseInUse:
                        pass
                    else:
                        second.close()
                        raise AssertionError("second host acquired same database")
                finally:
                    first.close()
            """
        )

    def test_static_demo_assets_are_served_without_external_dependencies(self):
        self.run_demo(
            r"""
            with http_demo() as (service,url):
                for route,content_type in (("/","text/html"),("/demo.js","text/javascript"),("/demo.css","text/css")):
                    with urlopen(url+route,timeout=5) as response:
                        assert response.headers["Content-Type"].startswith(content_type)
                        assert len(response.read()) > 100
                with urlopen(url,timeout=5) as response:
                    html=response.read().decode()
                assert "<script src=\"/demo.js\"" in html
                assert "https://" not in html
            """
        )

    def test_sql_implementation_errors_are_not_hidden_as_degradation(self):
        self.run_demo(
            r"""
            with repository() as repo:
                job,_=create(repo)
                with sqlite3.connect(repo.path) as db:
                    db.execute("DROP TABLE ui_stream_demo_jobs")
                try:
                    SQLiteArchiveStore(repo,scenario=Scenario.NORMAL).checkpoint(
                        binding=binding(job),events=(event(1),),archive_status=Integrity.COMPLETE)
                except sqlite3.OperationalError:
                    pass
                else:
                    raise AssertionError("SQL implementation error swallowed or reclassified")
            """
        )

    def test_future_cursor_requests_snapshot_reset_instead_of_fake_replay(self):
        self.run_demo(
            r"""
            with http_demo(interval=0.05) as (service,url):
                _,job=post(url,{"request_token":str(uuid4()),"scenario":"normal"})
                with urlopen(url+"/api/jobs/"+job["id"]+"/events?after=999",timeout=5) as response:
                    payload=response.read().decode()
                assert payload.startswith("event: reset\n") and "live_tail_gap" in payload
            """
        )

    def test_closed_producer_without_terminal_notification_requests_snapshot_sync(self):
        self.run_demo(
            r"""
            with http_demo(interval=0.2) as (service,url):
                _,job=post(url,{"request_token":str(uuid4()),"scenario":"normal"})
                source=urlopen(url+"/api/jobs/"+job["id"]+"/events?after=0",timeout=5)
                service.close()
                payload=source.read().decode()
                source.close()
                assert "event: sync" in payload
                assert service.get(job["id"])["status"] == "INTERRUPTED"
            """
        )

    def test_busy_host_does_not_create_queued_jobs_and_existing_token_still_works(self):
        self.run_demo(
            r"""
            from demos.sqlite.service import DemoBusy
            with tempfile.TemporaryDirectory() as folder:
                service=DemoService(Path(folder)/"demo.sqlite3",interval=0.2)
                try:
                    tokens=[str(uuid4()) for _ in range(4)]
                    first=[service.create(request_token=token,scenario=Scenario.NORMAL)[0] for token in tokens]
                    same,created=service.create(request_token=tokens[0],scenario=Scenario.NORMAL)
                    assert not created and same["id"] == first[0]["id"]
                    rejected=str(uuid4())
                    try:
                        service.create(request_token=rejected,scenario=Scenario.NORMAL)
                    except DemoBusy:
                        pass
                    else:
                        raise AssertionError("unbounded producer queue accepted")
                    assert service.repository.by_token(rejected) is None
                finally:
                    service.close()
            """
        )

    def test_persistent_sqlite_write_lock_leaves_unknown_terminal_and_closed_producer(self):
        self.run_demo(
            r"""
            from unittest.mock import patch
            with http_demo(interval=0.02) as (service,url):
                original_connect=sqlite3.connect
                def short_timeout(*args, **kwargs):
                    # Keep the real SQLite lock/error path; only shorten its wait in this test.
                    kwargs["timeout"]=0.01
                    return original_connect(*args, **kwargs)
                with patch("demos.sqlite.storage.sqlite3.connect", side_effect=short_timeout):
                    _,job=post(url,{"request_token":str(uuid4()),"scenario":"normal"})
                    lock=original_connect(service.repository.path,isolation_level=None)
                    lock.execute("BEGIN IMMEDIATE")
                    try:
                        with urlopen(url+"/api/jobs/"+job["id"]+"/events?after=0",timeout=5) as response:
                            payload=response.read().decode()
                        assert "event: sync" in payload and "event: finished" not in payload
                        saved=service.get(job["id"])
                        assert saved["status"] == "RUNNING" and saved["result"] is None
                        assert service.channel(job["id"]).read(0,timeout=0).closed
                    finally:
                        lock.rollback()
                        lock.close()
            """
        )

    def test_loopback_server_bind_does_not_require_reverse_dns(self):
        self.run_demo(
            r"""
            with tempfile.TemporaryDirectory() as folder:
                service=DemoService(Path(folder)/"demo.sqlite3")
                try:
                    with patch("socket.getfqdn", side_effect=AssertionError("loopback bind must not resolve DNS")):
                        server=make_server(service,port=0)
                    try:
                        assert server.server_name == "127.0.0.1" and server.server_port > 0
                    finally:
                        server.server_close()
                finally:
                    service.close()
            """
        )

"""Client-state regressions using existing Node; these are not browser/UI verification."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(shutil.which("node"), "Existing Node is needed for optional client-state tests")
class DemoClientContractTest(unittest.TestCase):
    def run_client(self, scenario):
        root = Path(__file__).resolve().parents[1] / "demos/sqlite"
        consumer = Path(os.environ.get("UI_STREAM_DEMO_PATH", str(root)))
        source = (consumer / "static/demo.js").read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "demo.js"
            script.write_bytes(source)
            result = subprocess.run(
                [
                    shutil.which("node"),
                    str(Path(__file__).with_name("demo_client_contract.cjs")),
                    str(script),
                    scenario,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pending_creation_takes_priority_over_old_job_url_on_refresh(self):
        self.run_client("pending_with_old_url")

    def test_closed_producer_does_not_immediately_reopen_sse(self):
        self.run_client("closed_producer")

    def test_older_snapshot_cannot_replace_a_confirmed_terminal_result(self):
        self.run_client("out_of_order_snapshot")

    def test_terminal_sse_state_invalidates_older_snapshot(self):
        self.run_client("terminal_state_snapshot")

    def test_terminal_sse_state_ignores_older_snapshot_error(self):
        self.run_client("terminal_state_error")

    def test_successful_recovery_clears_transient_read_error(self):
        self.run_client("recovered_snapshot_notice")

    def test_directory_consumer_uses_selected_client_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            asset = Path(folder) / "static/demo.js"
            asset.parent.mkdir(parents=True)
            asset.write_text('throw new Error("Selected consumer asset");')
            with patch.dict(os.environ, {"UI_STREAM_DEMO_PATH": folder}):
                with self.assertRaisesRegex(AssertionError, "Selected consumer asset"):
                    self.run_client("closed_producer")

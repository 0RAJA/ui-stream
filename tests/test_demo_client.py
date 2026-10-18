"""Client-state regressions using existing Node; these are not browser/UI verification."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile


@unittest.skipUnless(shutil.which("node"), "Existing Node is needed for optional client-state tests")
class DemoClientContractTest(unittest.TestCase):
    def run_client(self, scenario):
        root = Path(__file__).resolve().parents[1] / "src"
        package = os.environ.get("UI_STREAM_TEST_PATH")
        if package and package.endswith(".whl"):
            with ZipFile(package) as wheel:
                source = wheel.read("ui_stream/demo/static/demo.js")
        else:
            consumer = Path(package) if package else root
            source = (consumer / "ui_stream/demo/static/demo.js").read_bytes()
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

    def test_directory_consumer_uses_selected_client_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            asset = Path(folder) / "ui_stream/demo/static/demo.js"
            asset.parent.mkdir(parents=True)
            asset.write_text('throw new Error("Selected consumer asset");')
            with patch.dict(os.environ, {"UI_STREAM_TEST_PATH": folder}):
                with self.assertRaisesRegex(AssertionError, "Selected consumer asset"):
                    self.run_client("closed_producer")

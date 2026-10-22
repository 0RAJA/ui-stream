"""Verify that an untrusted bootstrap download never reaches the installer."""

import hashlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

try:
    from tools import bootstrap_pip
except ImportError:
    bootstrap_pip = None


class BootstrapPipTest(unittest.TestCase):
    def tool(self):
        self.assertIsNotNone(bootstrap_pip, "Hash-verified pip bootstrap must exist")
        return bootstrap_pip

    def test_digest_must_match_before_the_wheel_is_accepted(self):
        tool = self.tool()
        payload = b"synthetic wheel"
        tool.verify_wheel(payload, hashlib.sha256(payload).hexdigest())
        with self.assertRaises(ValueError):
            tool.verify_wheel(payload, "0" * 64)

    def test_untrusted_download_never_starts_the_installer(self):
        tool = self.tool()
        with (
            mock.patch.object(tool, "urlopen", return_value=io.BytesIO(b"untrusted wheel")),
            mock.patch.object(tool.subprocess, "run") as install,
        ):
            with self.assertRaises(ValueError):
                tool.bootstrap()
            install.assert_not_called()

    def test_oversized_download_never_starts_the_installer(self):
        tool = self.tool()
        with (
            mock.patch.object(tool, "urlopen", return_value=io.BytesIO(b"x" * (tool.MAX_WHEEL_BYTES + 1))),
            mock.patch.object(tool.subprocess, "run") as install,
        ):
            with self.assertRaises(ValueError):
                tool.bootstrap()
            install.assert_not_called()

    def test_verified_bytes_are_installed_with_no_index_or_dependencies(self):
        tool = self.tool()
        payload = b"synthetic wheel"

        def install(command, *, check):
            self.assertTrue(check)
            self.assertEqual(command[0], sys.executable)
            self.assertIn("--isolated", command)
            self.assertIn("--no-index", command)
            self.assertIn("--no-deps", command)
            self.assertEqual(Path(command[-1]).read_bytes(), payload)

        with (
            mock.patch.object(tool, "PIP_SHA256", hashlib.sha256(payload).hexdigest()),
            mock.patch.object(tool, "urlopen", return_value=io.BytesIO(payload)),
            mock.patch.object(tool.subprocess, "run", side_effect=install) as run,
        ):
            tool.bootstrap()
            run.assert_called_once()

    def test_unsupported_python_is_rejected_before_network_access(self):
        tool = self.tool()
        with (
            mock.patch.object(tool.sys, "version_info", (3, 9)),
            mock.patch.object(tool, "urlopen") as download,
        ):
            with self.assertRaises(RuntimeError):
                tool.bootstrap()
            download.assert_not_called()

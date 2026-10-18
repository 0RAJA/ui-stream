"""Release checks must flag leaks without copying their values into reports."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

try:
    from tools.check_release import check_member, check_project, scan_text
except ModuleNotFoundError:
    check_member = check_project = scan_text = None


class ReleaseCheckTest(unittest.TestCase):
    def test_secret_findings_do_not_echo_values(self):
        self.assertTrue(callable(scan_text), "release scanner must exist")
        value = "synthetic-example-" + "z" * 32
        result = scan_text("sample.py", ("api_key = '" + value + "'").encode())
        self.assertIn("credential_assignment", {item["category"] for item in result})
        self.assertNotIn(value, str(result))

    def test_private_network_and_machine_paths_are_flagged(self):
        self.assertTrue(callable(scan_text), "release scanner must exist")
        value = "http://service." + "corp.example.net/x " + "10." + "23.45.67 " + "/Users" + "/example/private"
        result = scan_text("sample.md", value.encode())
        self.assertTrue({"internal_host", "private_ip", "machine_path"} <= {x["category"] for x in result})
        self.assertNotIn(value, str(result))

    def test_public_links_and_local_demo_are_allowed(self):
        self.assertTrue(callable(scan_text), "release scanner must exist")
        value = "http://127.0.0.1:8765 https://packaging.python.org/ 00000000-0000-4000-8000-000000000001"
        self.assertEqual(scan_text("sample.md", value.encode()), [])

    def test_unlisted_environment_database_and_paths_fail(self):
        self.assertTrue(callable(check_member), "artifact allowlist checker must exist")
        allowed = {"README.md"}
        for name in (".env", "demo.sqlite3", "../README.md", "/README.md", "unused.txt"):
            with self.subTest(name=name):
                self.assertTrue(check_member(name, allowed))
        self.assertEqual(check_member("README.md", allowed), [])

    def test_inline_private_key_is_flagged(self):
        self.assertTrue(callable(scan_text), "release scanner must exist")
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        self.assertIn("private_key", {x["category"] for x in scan_text("sample.pem", marker.encode())})

    def test_sdist_cannot_hide_traversal_in_archive_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            (project / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "1.0"\n')
            (project / "README.md").write_text("synthetic")
            (project / "release-allowlist.txt").write_text("README.md\npyproject.toml\nrelease-allowlist.txt\n")
            dist = root / "dist"
            dist.mkdir()
            with tarfile.open(dist / "example-1.0.tar.gz", "w:gz") as archive:
                member = tarfile.TarInfo("../README.md")
                member.size = 9
                archive.addfile(member, io.BytesIO(b"synthetic"))
            report = check_project(project, dist)
            self.assertIn("invalid_archive_root", {finding["category"] for finding in report["findings"]})

    def test_two_wheels_do_not_replace_required_sdist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            (project / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "1.0"\n')
            (project / "release-allowlist.txt").write_text("pyproject.toml\nrelease-allowlist.txt\n")
            dist = root / "dist"
            dist.mkdir()
            for name in ("example-1.0-py3-none-any.whl", "example-1.0-py311-none-any.whl"):
                with ZipFile(dist / name, "w"):
                    pass
            result = check_project(project, dist)
            self.assertIn("wheel_and_sdist_required", {item["category"] for item in result["findings"]})

    def test_unsafe_archive_directories_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            (project / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "1.0"\n')
            (project / "release-allowlist.txt").write_text("pyproject.toml\nrelease-allowlist.txt\n")
            dist = root / "dist"
            dist.mkdir()
            with tarfile.open(dist / "example-1.0.tar.gz", "w:gz") as archive:
                member = tarfile.TarInfo("../../outside")
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            with ZipFile(dist / "example-1.0-py3-none-any.whl", "w") as archive:
                archive.writestr("../../outside/", "")
            result = check_project(project, dist)
            categories = {item["category"] for item in result["findings"]}
            self.assertIn("invalid_archive_root", categories)
            self.assertIn("unexpected_or_unsafe_member", categories)

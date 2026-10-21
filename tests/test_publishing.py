"""Release gates must reject mismatched tags and keep Git metadata out of payloads."""

import tempfile
import unittest
from pathlib import Path

from tools.check_release import check_project, expected_members, scan_text

try:
    from tools.verify_distribution import validate_release_tag
except ModuleNotFoundError:
    validate_release_tag = None


class PublishingTest(unittest.TestCase):
    def test_release_tag_matches_exact_metadata_version(self):
        self.assertTrue(callable(validate_release_tag), "release tag gate must exist")
        validate_release_tag("v0.1.0a1", "0.1.0a1")
        for tag in ("", "v0.1.0", "0.1.0a1", "refs/heads/main", "v0.1.0a1\n"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                validate_release_tag(tag, "0.1.0a1")

    def test_shell_metacharacters_are_rejected_as_tag_data(self):
        self.assertTrue(callable(validate_release_tag), "release tag gate must exist")
        for suffix in (";true", "$(true)", "`true`", "'", '"'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                validate_release_tag("v0.1.0a1" + suffix, "0.1.0a1")

    def test_source_scan_ignores_checkout_git_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            (project / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "1.0"\n')
            (project / "release-allowlist.txt").write_text("pyproject.toml\nrelease-allowlist.txt\n")
            (project / ".git").mkdir()
            (project / ".git/config").write_text("[core]\nrepositoryformatversion = 0\n")
            dist = root / "dist"
            dist.mkdir()
            result = check_project(project, dist)
            self.assertFalse(any(item["file"].startswith(".git/") for item in result["findings"]))

    def test_approved_public_contact_is_not_reported_as_a_secret(self):
        approved = b"raja <rajawwang@foxmail.com>"
        self.assertEqual(scan_text("pyproject.toml", approved), [])
        unapproved = ("someone" + "@" + "example.org").encode()
        self.assertIn("email_address", {item["category"] for item in scan_text("fixture", unapproved)})


class DistributionBoundaryTest(unittest.TestCase):
    def test_default_wheel_contains_only_runtime_and_type_marker(self):
        root = Path(__file__).resolve().parents[1]
        _, wheel, sdist = expected_members(root)
        payload = {name for name in wheel if ".dist-info/" not in name}
        expected = {
            "ui_stream/" + name
            for name in (
                "__init__.py",
                "_cursor.py",
                "_json.py",
                "archive.py",
                "codec.py",
                "exceptions.py",
                "reader.py",
                "redis.py",
                "runtime.py",
                "sse.py",
                "types.py",
                "py.typed",
            )
        }
        self.assertEqual(payload, expected)
        forbidden = ("demos/", "examples/", "tests/", "benchmarks/", "tools/", ".github/", "docs/")
        self.assertFalse(any(name.startswith(forbidden) for name in sdist))
        self.assertIn("README.pypi.md", sdist)

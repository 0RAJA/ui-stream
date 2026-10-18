"""Verify minimal artifacts with repository-owned tests and isolated consumers."""

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

from tools.check_release import check_project


def validate_release_tag(tag, version):
    if tag != f"v{version}":
        raise ValueError("Release tag must exactly match v + project.version")


def run(command, *, cwd, env):
    subprocess.run(command, cwd=cwd, env=env, check=True)


def consumer_tests(project, package, temporary, env):
    env = dict(env, UI_STREAM_TEST_PATH=str(package), PYTHONPATH="")
    script = (
        "import sys; sys.path[:0]=" + repr([str(package), str(project)]) + "; "
        "import ui_stream; "
        "assert ui_stream.__file__.startswith(" + repr(str(package)) + "), ui_stream.__file__; "
        "import unittest; unittest.main(module=None, argv="
        + repr(
            [
                "consumer",
                "tests.test_contract",
                "tests.test_runtime_boundaries",
                "tests.test_demo",
                "tests.test_demo_client",
                "-v",
            ]
        )
        + ")"
    )
    run([sys.executable, "-I", "-c", script], cwd=temporary, env=env)


def verify(project, dist):
    report = check_project(project, dist)
    if report["findings"]:
        raise ValueError(json.dumps(report["findings"]))
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    (wheel,) = dist.glob("*.whl")
    (sdist,) = dist.glob("*.tar.gz")
    stem = metadata["name"].replace("-", "_") + "-" + metadata["version"] + ".dist-info/"
    with ZipFile(wheel) as archive:
        headers = BytesParser().parsebytes(archive.read(stem + "METADATA"))
        assert headers["Name"] == metadata["name"]
        assert headers["Version"] == metadata["version"]
        assert headers["Requires-Python"] == metadata["requires-python"]
        assert headers["License-Expression"] == "MIT"
        assert headers["Author-email"] == headers["Maintainer-email"] == "raja <rajawwang@foxmail.com>"
        assert set(headers.get_all("License-File")) == {"LICENSE", "LICENSE.upstream", "NOTICE"}
        assert all("extra ==" in dependency for dependency in headers.get_all("Requires-Dist", []))
        for name in ("LICENSE", "LICENSE.upstream", "NOTICE"):
            assert archive.read(stem + "licenses/" + name) == (project / name).read_bytes()
        for name in (project / "release-allowlist.txt").read_text().splitlines():
            if name.startswith("src/"):
                assert archive.read(name.removeprefix("src/")) == (project / name).read_bytes(), name
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="")
    with tempfile.TemporaryDirectory(prefix="ui-stream-verify-") as folder:
        temporary = Path(folder)
        consumer_tests(project, wheel, temporary, env)
        installed = temporary / "installed"
        run(
            [
                sys.executable,
                "-m",
                "pip",
                "--isolated",
                "install",
                "--no-index",
                "--no-deps",
                "--no-compile",
                "--target",
                str(installed),
                str(wheel),
            ],
            cwd=temporary,
            env=env,
        )
        consumer_tests(project, installed, temporary, env)
        # -S hides all third-party site packages: this proves the core plus repository
        # host example works without the optional JSON/Redis profiles.
        script = (
            "import sys; sys.path[:0]=" + repr([str(installed), str(project)]) + "; "
            "import importlib.metadata, runpy, ui_stream; "
            "assert importlib.metadata.version('ui-stream')==" + repr(metadata["version"]) + "; "
            "assert ui_stream.__file__.startswith(" + repr(str(installed)) + "); "
            "runpy.run_module('examples.counter',run_name='__main__')"
        )
        run([sys.executable, "-I", "-S", "-c", script], cwd=temporary, env=env)
        source = temporary / "source"
        with tarfile.open(sdist) as archive:
            archive.extractall(source, filter="data")
        (extracted,) = source.iterdir()
        for name in (project / "release-allowlist.txt").read_text().splitlines():
            assert (extracted / name).read_bytes() == (project / name).read_bytes(), name
        rebuilt = temporary / "rebuilt"
        rebuilt.mkdir()
        script = "from setuptools.build_meta import build_wheel; build_wheel(" + repr(str(rebuilt)) + ")"
        run([sys.executable, "-c", script], cwd=extracted, env=env)
        (rebuilt_wheel,) = rebuilt.glob("*.whl")
        with ZipFile(wheel) as first, ZipFile(rebuilt_wheel) as second:
            assert set(first.namelist()) == set(second.namelist())
            assert all(first.read(name) == second.read(name) for name in first.namelist())
        # Tests and hosts deliberately stay outside the extracted minimal sdist.
        consumer_tests(project, rebuilt_wheel, temporary, env)
    print(json.dumps({"verified": metadata["version"], "artifacts": report["artifacts"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--check-tag")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    version = tomllib.loads((project / "pyproject.toml").read_text())["project"]["version"]
    if args.check_tag is not None:
        validate_release_tag(args.check_tag, version)
    if args.dist is not None:
        verify(project, args.dist.resolve())
    elif args.check_tag is None:
        parser.error("--dist or --check-tag is required")


if __name__ == "__main__":
    main()

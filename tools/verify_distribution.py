"""Verify built distributions and isolated consumers without uploading or installing."""

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


def verify(project, dist):
    report = check_project(project, dist)
    if report["findings"]:
        raise ValueError(json.dumps(report["findings"]))
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    wheel = next(dist.glob("*.whl"))
    sdist = next(dist.glob("*.tar.gz"))
    stem = metadata["name"].replace("-", "_") + "-" + metadata["version"] + ".dist-info/"
    with ZipFile(wheel) as archive:
        headers = BytesParser().parsebytes(archive.read(stem + "METADATA"))
        assert headers["Name"] == metadata["name"]
        assert headers["Version"] == metadata["version"]
        assert headers["Requires-Python"] == metadata["requires-python"]
        assert headers["License-Expression"] == "MIT"
        assert set(headers.get_all("License-File")) == {"LICENSE", "LICENSE.upstream", "NOTICE"}
        assert all("extra ==" in dependency for dependency in headers.get_all("Requires-Dist", []))
        for name in ("LICENSE", "LICENSE.upstream", "NOTICE"):
            assert archive.read(stem + "licenses/" + name) == (project / name).read_bytes()
        for name in (project / "release-allowlist.txt").read_text().splitlines():
            if name.startswith("src/"):
                assert archive.read(name.removeprefix("src/")) == (project / name).read_bytes(), name
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", UI_STREAM_TEST_PATH=str(wheel))
    tests = ["-m", "unittest", "tests.test_contract", "tests.test_demo", "tests.test_demo_client", "-v"]
    run([sys.executable, *tests], cwd=project, env=env)
    with tempfile.TemporaryDirectory(prefix="ui-stream-verify-") as folder:
        temporary = Path(folder)
        installed = temporary / "installed"
        with ZipFile(wheel) as archive:
            archive.extractall(installed)
        env["UI_STREAM_TEST_PATH"] = str(installed)
        run([sys.executable, *tests], cwd=project, env=env)
        source = temporary / "source"
        with tarfile.open(sdist) as archive:
            archive.extractall(source, filter="data")
        extracted = next(source.iterdir())
        for name in (project / "release-allowlist.txt").read_text().splitlines():
            assert (extracted / name).read_bytes() == (project / name).read_bytes(), name
        rebuilt = temporary / "rebuilt"
        rebuilt.mkdir()
        script = "from setuptools.build_meta import build_wheel; build_wheel(" + repr(str(rebuilt)) + ")"
        run([sys.executable, "-c", script], cwd=extracted, env=env)
        rebuilt_wheel = next(rebuilt.glob("*.whl"))
        with ZipFile(wheel) as first, ZipFile(rebuilt_wheel) as second:
            assert set(first.namelist()) == set(second.namelist())
            assert all(first.read(name) == second.read(name) for name in first.namelist())
        env["UI_STREAM_TEST_PATH"] = str(rebuilt_wheel)
        env["PYTHONPATH"] = str(extracted / "src")
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=extracted, env=env)
        script = (
            "import sys; sys.path.insert(0," + repr(str(rebuilt_wheel)) + "); "
            "import importlib.metadata, runpy, ui_stream; "
            "assert importlib.metadata.version('ui-stream')==" + repr(metadata["version"]) + "; "
            "assert '.whl/' in ui_stream.__file__; "
            "runpy.run_module('ui_stream.example',run_name='__main__')"
        )
        run([sys.executable, "-I", "-S", "-c", script], cwd=temporary, env=env)
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

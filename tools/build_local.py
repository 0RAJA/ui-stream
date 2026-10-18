"""Build locally with existing setuptools, without dependency installation or uploads."""

import argparse
import os
from pathlib import Path

from setuptools.build_meta import build_sdist, build_wheel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    build_sdist(str(output))
    build_wheel(str(output))


if __name__ == "__main__":
    main()

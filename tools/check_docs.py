"""Check bilingual runnable blocks, local links, and the generated PyPI README."""

import argparse
import re
from pathlib import Path
from urllib.parse import urlsplit

LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^\s)]+)\)")
BASE = "https://github.com/0RAJA/ui-stream/blob/main/"
RAW = "https://raw.githubusercontent.com/0RAJA/ui-stream/main/"


def render_pypi(text):
    def replace(match):
        image, label, destination = match.groups()
        if urlsplit(destination).scheme or destination.startswith(("#", "//")):
            return match.group()
        return f"{image}[{label}]({RAW if image else BASE}{destination.removeprefix('./')})"

    return LINK.sub(replace, text)


def validate_commands(chinese, english):
    pattern = r"```(?:sh|bash|python)\n(.*?)```"
    blocks = [re.findall(pattern, text, re.DOTALL) for text in (chinese, english)]
    if blocks[0] != blocks[1]:
        raise ValueError("Bilingual runnable command blocks diverge")


def check(project, *, write=False):
    files = sorted(project.glob("*.md")) + sorted((project / "docs").rglob("*.md"))
    files += sorted((project / "demos").rglob("*.md"))
    pairs = 0
    for path in files:
        if path.name.endswith(".en.md") or path.name == "README.pypi.md":
            continue
        english = path.with_name(path.stem + ".en.md")
        if not english.is_file():
            raise ValueError(f"Missing English counterpart: {path.relative_to(project)}")
        chinese_text, english_text = path.read_text(), english.read_text()
        if f"]({english.name})" not in chinese_text or f"]({path.name})" not in english_text:
            raise ValueError(f"Missing bilingual navigation: {path.relative_to(project)}")
        validate_commands(chinese_text, english_text)
        pairs += 1
    for path in files:
        text = path.read_text()
        for _, _, destination in LINK.findall(text):
            parsed = urlsplit(destination)
            if parsed.scheme or destination.startswith(("#", "//")):
                continue
            target = (path.parent / parsed.path).resolve()
            if not target.is_relative_to(project) or not target.is_file():
                raise ValueError(f"Broken local link: {path.relative_to(project)} -> {destination}")
        if "python -m ui_stream." + "demo" in text or "python -m ui_stream." + "example" in text:
            raise ValueError(f"Obsolete bundled example command: {path.relative_to(project)}")
    source = (project / "README.md").read_text()
    if "```mermaid" in source:
        raise ValueError("PyPI README must use static diagrams")
    expected = render_pypi(source)
    target = project / "README.pypi.md"
    if write:
        target.write_text(expected)
    if not target.is_file() or target.read_text() != expected:
        raise ValueError("Regenerate README.pypi.md with --write-pypi")
    for path in (project / "docs").rglob("*.svg"):
        text = path.read_text()
        if not all(marker in text for marker in ('role="img"', '<title id=', '<desc id=')):
            raise ValueError(f"SVG lacks accessible description: {path.name}")
        if "<script" in text or "<image" in text:
            raise ValueError(f"SVG must be self-contained vector geometry: {path.name}")
        if not path.with_suffix('.png').read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"Missing PNG fallback: {path.name}")
    print(f"Verified {pairs} bilingual pairs, local links, static diagrams and PyPI README")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-pypi", action="store_true")
    args = parser.parse_args()
    check(Path(__file__).resolve().parents[1], write=args.write_pypi)


if __name__ == "__main__":
    main()

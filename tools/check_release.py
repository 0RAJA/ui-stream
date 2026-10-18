"""Local pattern/allowlist checks. Reports include categories and paths, never matches.

This intentionally conservative stdlib check is not a credential service, legal
review, high-entropy scanner, or proof that all personal/internal data was removed.
"""

import argparse
import hashlib
import ipaddress
import json
import re
import tarfile
import tomllib
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

PUBLIC_CONTACTS = frozenset({"rajawwang@foxmail.com"})

PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "credential_assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|authorization)\b"
        r"\s*[:=]\s*['\"][^'\"\s]{16,}['\"]"
    ),
    "credential_url": re.compile(r"https?://[^\s/@:]+:[^\s/@]+@"),
    "known_token": re.compile(r"\b(?:AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{32,})\b"),
    "internal_host": re.compile(r"(?i)\b(?:[\w-]+\.)+(?:internal|local|corp|lan)\b|\b[\w-]+\.corp\.[\w.-]+"),
    "private_repository": re.compile(r"(?i)git" + r"@[^\s]+|https?://(?:git|gitlab|svn|code)\.[^\s/'\"]+"),
    "machine_path": re.compile(r"/(?:Users|home)/[^/\s]+/|[A-Za-z]:\\Users\\[^\\\s]+\\"),
    "email_address": re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
}
IP_PATTERN = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
LEGAL_FILES = {"LICENSE", "LICENSE.upstream", "NOTICE", "PROVENANCE.md"}
SENSITIVE_SUFFIXES = (".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".db", ".log", ".pyc", ".pem", ".p12")


def scan_text(name, data):
    text = data.decode("utf-8", errors="replace")
    findings = []
    for category, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            if category == "email_address" and match.group() in PUBLIC_CONTACTS:
                continue
            findings.append({"file": name, "line": text.count("\n", 0, match.start()) + 1, "category": category})
    for match in IP_PATTERN.finditer(text):
        try:
            address = ipaddress.ip_address(match.group())
        except ValueError:
            continue
        if address.is_private and not address.is_loopback:
            findings.append({"file": name, "line": text.count("\n", 0, match.start()) + 1, "category": "private_ip"})
    if PurePosixPath(name).name not in LEGAL_FILES:
        for match in re.finditer(r"(?i)\bbk[-_]audit\b|" + "blue" + "king|" + "ten" + "cent|蓝" + "鲸|审计" + "中心", text):
            # Copyright/attribution must be preserved, not redacted as a secret.
            line = text[
                text.rfind("\n", 0, match.start()) + 1 : text.find("\n", match.start())
                if "\n" in text[match.start() :]
                else len(text)
            ]
            if "Copyright" not in line:
                findings.append(
                    {"file": name, "line": text.count("\n", 0, match.start()) + 1, "category": "host_identifier"}
                )
    return findings


def check_member(name, allowed):
    path = PurePosixPath(name)
    unsafe = path.is_absolute() or ".." in path.parts or "\\" in name
    sensitive = any(part in {".git", ".venv", "__pycache__", ".env", ".pypirc"} for part in path.parts)
    sensitive = sensitive or path.name.startswith(".env") or name.endswith(SENSITIVE_SUFFIXES)
    if unsafe or sensitive or name not in allowed:
        return [{"file": name, "category": "unexpected_or_unsafe_member"}]
    return []


def expected_members(project):
    source = set((project / "release-allowlist.txt").read_text().splitlines())
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    stem = metadata["name"].replace("-", "_") + "-" + metadata["version"]
    dist_info = stem + ".dist-info/"
    wheel = {name.removeprefix("src/") for name in source if name.startswith("src/")}
    wheel |= {dist_info + name for name in ("METADATA", "WHEEL", "RECORD", "top_level.txt")}
    wheel |= {dist_info + "licenses/" + name for name in ("LICENSE", "LICENSE.upstream", "NOTICE")}
    generated = {"PKG-INFO", "setup.cfg"}
    generated |= {"src/ui_stream.egg-info/" + name for name in ("SOURCES.txt",)}
    return source, wheel, source | generated


def check_project(project, dist):
    source, wheel_allowed, sdist_allowed = expected_members(project)
    report = {"source_files": 0, "artifacts": [], "findings": []}
    # Build/cache files are never release input. Only allowlisted input is read;
    # any other project file (including symlinks) causes an explicit finding.
    for path in sorted(project.rglob("*")):
        name = path.relative_to(project).as_posix()
        if any(
            part in {".git", "build", "dist", "__pycache__"} or part.endswith(".egg-info")
            for part in path.relative_to(project).parts
        ):
            continue
        if path.is_dir() and not path.is_symlink():
            continue
        report["findings"] += check_member(name, source)
        if path.is_symlink():
            report["findings"].append({"file": name, "category": "symlink"})
        elif path.is_file():
            report["source_files"] += 1
            report["findings"] += scan_text(name, path.read_bytes())
    for missing in source - {p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file()}:
        report["findings"].append({"file": missing, "category": "missing_source"})
    seen_types = set()
    wheel_dirs = {str(parent) for name in wheel_allowed for parent in PurePosixPath(name).parents if str(parent) != "."}
    sdist_dirs = {str(parent) for name in sdist_allowed for parent in PurePosixPath(name).parents if str(parent) != "."}
    for artifact in sorted(dist.iterdir()):
        if artifact.suffix == ".whl":
            seen_types.add("wheel")
            with ZipFile(artifact) as archive:
                entries = []
                for entry in archive.infolist():
                    special = (entry.external_attr >> 16) & 0o170000 == 0o120000
                    if entry.is_dir():
                        report["findings"] += check_member(entry.filename.rstrip("/"), wheel_dirs)
                        if special:
                            report["findings"].append({"file": entry.filename, "category": "archive_link_or_special"})
                        continue
                    entries.append((entry.filename, archive.read(entry), special))
            allowed = wheel_allowed
        elif artifact.name.endswith(".tar.gz"):
            seen_types.add("sdist")
            with tarfile.open(artifact, "r:gz") as archive:
                entries = []
                for entry in archive.getmembers():
                    if entry.isdir() and entry.name.rstrip("/") == artifact.name[:-7]:
                        continue
                    parts = PurePosixPath(entry.name).parts
                    valid_root = (
                        len(parts) > 1
                        and parts[0] == artifact.name[:-7]
                        and ".." not in parts
                        and not entry.name.startswith("/")
                    )
                    if not valid_root:
                        report["findings"].append({"file": entry.name, "category": "invalid_archive_root"})
                    name = "/".join(parts[1:]) if valid_root else entry.name
                    if entry.isdir():
                        report["findings"] += check_member(name, sdist_dirs)
                        continue
                    stream = archive.extractfile(entry) if entry.isfile() else None
                    entries.append((name, stream.read() if stream else b"", not entry.isfile()))
            allowed = sdist_allowed
        else:
            report["findings"].append({"file": artifact.name, "category": "unexpected_artifact"})
            continue
        names = [entry[0] for entry in entries]
        report["artifacts"].append(
            {"name": artifact.name, "files": len(names), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
        )
        if len(names) != len(set(names)):
            report["findings"].append({"file": artifact.name, "category": "duplicate_archive_member"})
        for name, data, special in entries:
            report["findings"] += check_member(name, allowed)
            if special:
                report["findings"].append({"file": name, "category": "archive_link_or_special"})
            report["findings"] += scan_text(name, data)
        for missing in allowed - set(names):
            report["findings"].append({"file": missing, "category": "missing_archive_member"})
    if seen_types != {"wheel", "sdist"}:
        report["findings"].append({"file": "dist", "category": "wheel_and_sdist_required"})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    args = parser.parse_args()
    report = check_project(args.project.resolve(), args.dist.resolve())
    print(json.dumps(report, indent=2))
    return bool(report["findings"])


if __name__ == "__main__":
    raise SystemExit(main())

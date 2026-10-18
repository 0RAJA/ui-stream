# Contributing

[简体中文](CONTRIBUTING.md) · [Documentation](docs/README.en.md)

Open PRs at [0RAJA/ui-stream](https://github.com/0RAJA/ui-stream) with synthetic reproductions and tests. Use the [private channel](SECURITY.en.md) for vulnerabilities; never post credentials, private logs, or business data.
Contributions use MIT with existing attribution retained. Ensure submission rights; this document creates no CLA or rights assignment.

Use Python ≥3.11 and a virtual environment. Full tests need the declared JSON/Redis extras. Client-state unit tests use an existing Node, no npm packages, and explicitly skip if unavailable.

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m tools.check_docs
python tools/build_local.py --output ./dist
python tools/check_release.py --project . --dist ./dist
python -m tools.verify_distribution --dist ./dist
```

If pre-commit is available, run `pre-commit run --all-files`. Keep core dependencies empty, imports at module scope, and adapters outside the core. Add behavior regressions for commit, fencing, capacity, and exception ordering.
Individually update `repository-allowlist.txt` for repository files. Only core build inputs belong in `release-allowlist.txt` and `MANIFEST.in`. Inspect actual archives; exclude demos, static assets, tests, benchmarks, and development tools.

Chinese is primary; link English counterparts and keep commands, APIs, versions, and behavior aligned. Keep README focused on purpose, startup, boundaries, and entry points; put details in docs.
`README.pypi.md` is generated from the Chinese README with absolute links: run `python -m tools.check_docs --write-pypi` after editing. Retain SVG sources for static images; Mermaid alone is insufficient.

Release scanning expects a clean clone/export. Keep virtual environments and Demo SQLite data outside that tree. A development `.venv/` or `demo.sqlite3` is deliberately rejected by the strict allowlist. Use clean inputs for release verification rather than relaxing the gate to hide extra files.

# Contributing

[简体中文](CONTRIBUTING.md) · [Documentation](docs/README.en.md)

Open PRs at [0RAJA/ui-stream](https://github.com/0RAJA/ui-stream) with synthetic reproductions and tests. Use the [private channel](SECURITY.en.md) for vulnerabilities; never post credentials, private logs, or business data.
Contributions use MIT with existing attribution retained. Ensure submission rights; this document creates no CLA or rights assignment.

## Development checks

Use Python ≥3.11 and an activated virtual environment. Full tests need declared JSON/Redis extras; building needs the declared setuptools. Client-state tests use an existing Node, no npm packages, and explicitly skip if unavailable. These commands do not install tools/dependencies automatically.

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m tools.check_docs
```

If pre-commit is available, run `pre-commit run --all-files`. Keep core dependencies empty, imports at module scope, and adapters outside core. Add behavior regressions for commit ordering, stale-writer isolation, capacity, and exceptions.

## Documentation and distributions

Chinese is primary; link English counterparts and align commands, APIs, versions, and behavior. README introduces purpose/startup, Architecture explains concepts/integration, and API owns parameter/failure semantics. Avoid repeating the entire contract across pages. Comments explain purpose and rationale first, retaining useful input/output detail rather than targeting brevity.

`README.pypi.md` is generated from the Chinese README with absolute links: run `python -m tools.check_docs --write-pypi` after editing. Retain SVG sources for static images; Mermaid alone is insufficient.

Release scanning expects a clean clone/export, not a development tree with a virtual environment, caches, or Demo data. The following exports tracked files from the **index**, including staged uncommitted changes, to a new temporary directory before building/checking. Confirm the index contains the intended candidate; unstaged edits are not exported. Keep your virtual environment active without copying it into the export.

```sh
release_source="$(mktemp -d)"
git checkout-index --all --prefix="$release_source/"
cd "$release_source"
python tools/build_local.py --output ./dist
python tools/check_release.py --project . --dist ./dist
python -m tools.verify_distribution --dist ./dist
```

`repository-allowlist.txt` lists repository files individually; update it for additions. Only core build inputs belong in `release-allowlist.txt` and `MANIFEST.in`. Inspect actual archives; exclude demos, static assets, tests, benchmarks, and development tools. Preserve the strict allowlist rather than relaxing it to hide extra files.

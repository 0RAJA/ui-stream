# Contributing

Development takes place at [0RAJA/ui-stream](https://github.com/0RAJA/ui-stream).
Open a pull request with a synthetic reproduction and relevant tests. Public issues
are for non-sensitive bugs; use the private channel in SECURITY.md for vulnerabilities.
Do not include private application data, credentials or unpublished corporate material.
Contributions use MIT with existing attribution preserved; ensure you have permission
to submit your changes. No CLA or rights assignment is implied by this document.

Use a virtual environment and Python >=3.11. Core and Demo have no runtime dependencies.
Full contracts use the optional JSON/Redis dependencies; client-state tests use an
already installed Node and skip explicitly if it is unavailable. No npm dependency.

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python tools/build_local.py --output ./dist
UI_STREAM_TEST_PATH=./dist/ui_stream-0.1.0a1-py3-none-any.whl   python -m unittest tests.test_contract tests.test_demo tests.test_demo_client -v
python tools/check_release.py --project . --dist ./dist
```

Use an absolute wheel path for subprocess tests when invoking outside the project.
Use existing Black/isort/Flake8 tools if available; do not silently install tools.
Keep imports at module scope and adapters outside the core. Add a regression contract
for behavior changes, especially commit failure, fencing, capacity limits and retry
ordering. Never use real user names, logs or business data in tests. The Node client
fixtures execute the actual demo.js but are not real browser UI tests.

Add every intentional source/document/test asset to release-allowlist.txt. Regenerate
MANIFEST.in from that list when files change; include only individually reviewed paths.
Review the actual wheel and sdist, not just the source tree. Public-facing changes
require a changelog entry and source/license review.

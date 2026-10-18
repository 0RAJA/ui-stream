# Releasing

[简体中文](releasing.md) · [Documentation](README.en.md)

The active Release workflow only verifies artifacts. The [PyPI publishing job template](pypi-publish-job.yml) is inactive; no PyPI secret/token is configured.

## Verification

| Stage | Required checks |
| --- | --- |
| main / PR | Python 3.11–3.14 × Linux/macOS source; Linux distributions, README, docs, scans, and consumers |
| GitHub Release | Same checks; tag equals `v` + metadata version; commit belongs to main |
| PyPI | Upload this run's verified artifacts; check index metadata/digests and run consumers against the downloaded wheel |

```sh
python -m tools.check_docs
PYTHONPATH=src python -m unittest discover -s tests -v
python tools/build_local.py --output ./dist
python -m twine check --strict dist/*
python tools/check_release.py --project . --dist dist
python -m tools.verify_distribution --dist dist --check-tag v0.1.0a1
```

Use `v0.1.0a1` and the prerelease flag for this alpha. Required CI must cover the release commit; creating a GitHub Release does not confirm a PyPI upload.

## Activate the Trusted Publisher

Before configuring persistent trust, obtain explicit maintainer approval and verify the PyPI account through login:

| Setting | Value |
| --- | --- |
| PyPI project / account | `ui-stream` / `rajawwang` |
| GitHub owner / repo | `0RAJA/ui-stream` |
| Workflow / environment | `release.yml` / `pypi` |
| OIDC | `id-token: write` on the publisher job only |

1. Configure and read back the GitHub `pypi` environment: allow only `v*` tags, with required reviewer `0RAJA`. Stop if these protections are unsupported.
2. Add the PyPI pending publisher, then append the template to `.github/workflows/release.yml`. Pending publishers do not reserve names; recheck before first publication.
3. The maintainer approves GitHub Review deployments; automation must not approve on their behalf or bypass review. Copy no API token.

Official: [pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/), [OIDC publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

## Distribution boundary

The wheel contains nine `ui_stream` runtime modules, `py.typed`, and required metadata/licenses. The minimal sdist retains core rebuild inputs and the PyPI README. Demos, examples, tests, benchmarks, CI, and tools stay in the repository; extras only add dependencies.

`repository-allowlist.txt` checks the repository; `release-allowlist.txt` and `MANIFEST.in` bound the sdist. The gate installs the actual wheel outside the repository, loads repository tests and Demo hosts, checks core import locations, then rebuilds from sdist and compares every wheel member. Use a clean clone/export; see [Contributing](../CONTRIBUTING.en.md).

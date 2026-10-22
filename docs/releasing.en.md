# Releasing

[简体中文](releasing.md) · [Documentation](README.en.md)

CI bootstraps pip from a fixed official wheel and SHA-256 before downloading dependencies. Publishing pins an official Action commit with updated certifi and sigstore. Verify host pip, container pip, and requests using system CAs such as OIDC/TUF separately; updating external certifi does not cover every TLS request.

The Release workflow verifies artifacts before publishing to PyPI through OIDC and the protected `pypi` environment. The [publishing job reference](pypi-publish-job.yml) lists its permissions and steps.

The publishing step sets `SSL_CERT_FILE`, `SSL_CERT_DIR`, and `REQUESTS_CA_BUNDLE` to the fixed image's patched certifi. Its package directory has no OpenSSL hashed-certificate entries, preventing fallback to system CA directories. This affects only publishing-step processes, retains certificate/hostname verification and existing attestations, and does not modify host trust. When updating the pinned Action commit, recheck container paths, certificate contents, and the OIDC/TUF/upload clients.

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
release_tag="$(python -c 'import tomllib; from pathlib import Path; print("v" + tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"])')"
python -m tools.verify_distribution --dist dist --check-tag "$release_tag"
```

Tags use `v` plus the metadata version; mark alpha and other prereleases as prereleases on GitHub. Required CI must cover the release commit. Creating a GitHub Release does not confirm a PyPI upload.

## Trusted Publisher configuration

Publishing trust is bound to the following account, repository, workflow, and environment. Before changing this persistent trust, obtain explicit maintainer approval and verify the PyPI account through login:

| Setting | Value |
| --- | --- |
| PyPI project / account | `ui-stream` / `rajawwang` |
| GitHub owner / repo | `0RAJA/ui-stream` |
| Workflow / environment | `release.yml` / `pypi` |
| OIDC | `id-token: write` on the publisher job only |

1. Preserve and read back the GitHub `pypi` environment protections: allow only `v*` tags, with required reviewer `0RAJA`. Stop if these protections are unsupported.
2. Verify that the existing PyPI Trusted Publisher matches the repository, workflow, and environment. Publishing uses the workflow OIDC identity without introducing an API token.
3. The maintainer approves GitHub Review deployments; automation must not approve on their behalf or bypass review. Copy no API token.

Official: [Trusted Publishers](https://docs.pypi.org/trusted-publishers/), [OIDC publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

## Distribution boundary

The wheel contains `ui_stream` runtime modules and their private standard-library helpers, `py.typed`, and required metadata/licenses. The minimal sdist retains core rebuild inputs and the PyPI README. Demos, examples, tests, benchmarks, CI, and tools stay in the repository; extras only add dependencies.

`repository-allowlist.txt` checks the repository; `release-allowlist.txt` and `MANIFEST.in` bound the sdist. The gate installs the actual wheel outside the repository, loads repository tests and Demo hosts, checks core import locations, then rebuilds from sdist and compares every wheel member. Use a clean clone/export; see [Contributing](../CONTRIBUTING.en.md).

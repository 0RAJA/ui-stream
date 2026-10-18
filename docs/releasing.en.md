# Releasing

[简体中文](releasing.md) · [Documentation](README.en.md)

Keep the repository private during preparation. Do not publish to PyPI or create a public Release until CI, bilingual docs, security checks, and publishing setup are ready; then restore public visibility.

| Stage | Required gates |
| --- | --- |
| main / PR | Python 3.11–3.14 × Linux/macOS source; Linux distributions, README/docs, scanning, consumers |
| GitHub Release | Same gates; tag equals `v` + metadata version; commit is reachable from main |
| PyPI | Publish this run's verified artifacts; follow actual upload, index metadata, downloaded consumers |

```sh
python -m tools.check_docs
python -m unittest discover -s tests -v
python tools/build_local.py --output ./dist
python -m twine check --strict dist/*
python tools/check_release.py --project . --dist dist
python -m tools.verify_distribution --dist dist --check-tag v0.1.0a1
```

## Explicit approval before OIDC activation

The active Release workflow verifies only. The [publishing job template](pypi-publish-job.yml) is inactive; no PyPI secret/token is configured.
The maintainer must approve these exact persistent trust settings before changes; verify the PyPI account through an actual login:

| Setting | Value |
| --- | --- |
| PyPI project | `ui-stream` |
| PyPI account | `rajawwang` |
| GitHub owner / repo | `0RAJA/ui-stream` |
| Workflow | `release.yml` |
| Environment | `pypi` |
| OIDC | `id-token: write` |

Restrict the GitHub `pypi` environment to `v*` tags and reviewer `0RAJA`. Grant OIDC only to the publisher job; copy no API token.
After approval, add the PyPI pending publisher and GitHub environment, then append the template job to `.github/workflows/release.yml`.
A pending publisher does not reserve the project name; recheck immediately before first publication.

Use tag `v0.1.0a1` and a prerelease flag for this alpha. Wait for all required CI on the latest commit; creating a Release is not successful publication.
Check PyPI name/version/MIT/authors/URLs/digests and run consumers against the actual downloaded wheel.
Report exhausted free Actions quota; do not purchase resources or expand permissions.

Official: [pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/), [OIDC publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

## Distribution boundary

The default wheel contains only nine `ui_stream` runtime modules, `py.typed`, and required metadata/licenses.
The minimal sdist retains core rebuild inputs and the PyPI README. Demos, examples, tests, benchmarks, CI, and tools stay in the repository.
`repository-allowlist.txt` checks the repository; `release-allowlist.txt` and `MANIFEST.in` bound the sdist.
The gate installs the actual wheel outside the repository, loads repository tests and Demo hosts, checks the core import location, and compares every rebuilt wheel member from the sdist.
Extras add dependencies; they do not hide distribution files.

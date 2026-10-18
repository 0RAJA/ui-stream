# Releases and PyPI publishing

Repository: [0RAJA/ui-stream](https://github.com/0RAJA/ui-stream).
Maintainer: [raja (@0RAJA)](https://github.com/0RAJA). Initial alpha version: 0.1.0a1.
Upstream MIT/attribution is preserved; the maintainer has authorized public distribution.

## CI and release artifact gates

Pushes to main and pull requests run `.github/workflows/ci.yml` with read-only
repository permissions. It tests Python 3.11–3.14 on Linux/macOS and installs only
the declared optional profiles plus ephemeral CI tools. A subsequent Linux job
builds wheel/sdist, validates README/metadata with Twine, scans source and actual
archives, verifies isolated wheel and directory consumers, rebuilds from sdist,
and uploads the verified distributions as a run artifact.

A published GitHub Release triggers `.github/workflows/release.yml`, which repeats
the same checks against the release tag. The tag must equal `v` plus project.version
and the tag commit must be reachable from main. Do not put event values directly
into executable shell source; the workflow passes the tag through a quoted env value.

## Pending publishing activation

The active Release workflow currently validates artifacts only. The ready
[publishing job](pypi-publish-job.yml) is not active. No publishing secret or PyPI
Trusted Publisher has been created by this repository preparation.

Before activation, the maintainer must explicitly approve this exact configuration:

| Setting | Value |
| --- | --- |
| PyPI project | ui-stream |
| GitHub owner | 0RAJA |
| GitHub repository | ui-stream |
| Workflow filename | release.yml |
| GitHub environment | pypi |
| OIDC permission | id-token: write, publisher job only |
| Account | Maintainer's verified PyPI account; not inferred from GitHub username |

After approval, configure the PyPI pending publisher in that PyPI account, set up
the GitHub `pypi` environment restricted to release tags and the maintainer reviewer,
and append the reviewed publishing job under jobs in release.yml. The official
PyPA action exchanges OIDC for short-lived publication credentials; no API token
needs to be copied into the repository or Actions secrets. Do not add trust, tokens,
secrets or permanent authorizations without the maintainer's specific confirmation.

The pending publisher does not reserve the PyPI name. Recheck availability immediately
before first publish. If an index denies the name, choose a new name with the maintainer
and update all metadata and workflow/package references before rebuilding.

## Release procedure

1. Update project.version and CHANGELOG, review attribution and allowed source files.
2. Run local tests and `python -m build`; run `python -m twine check --strict dist/*`,
   `python tools/check_release.py --project . --dist dist` and
   `python -m tools.verify_distribution --dist dist`.
3. Merge/push the reviewed commit, wait for the actual CI checks and inspect artifacts.
4. Tag the verified commit `v0.1.0a1` (or its actual version), then publish a GitHub
   Release. For alpha versions, mark the Release as a prerelease.
5. Complete any configured environment approval. Follow Release Actions to completion;
   do not call an upload successful merely because a Release was created.
6. Verify PyPI name/version/license/URLs/digests, download the published wheel from PyPI,
   and run the same isolated consumer checks against the published bytes.

Local builds can use `python tools/build_local.py --output ./dist` with existing
setuptools, without fetching dependencies. CI uses clean hosted runners and declared
build dependencies. Python package installation is always in a virtual environment
or the disposable runner, never via copied credentials.

Official references: [PyPI pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/),
[PyPI OIDC workflow](https://docs.pypi.org/trusted-publishers/using-a-publisher/),
[packaging guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/).

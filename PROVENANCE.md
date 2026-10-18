# Source provenance

This is a source-only export; it contains no parent Git history, credentials,
application models, configuration, data, or deployment materials.
The origin is BlueKing - Audit Center (bk-audit), repository-level MIT declaration.
The original declaration is retained in [LICENSE.upstream](LICENSE.upstream) and
[NOTICE](NOTICE); no different upstream license was found within the selected files.
No third-party library source, fonts, or images are vendored.

## Extracted or derived

The following modules were extracted/generalized from the host streaming code.
New contracts and implementations were added during that refactoring; the whole
module is conservatively treated as derived rather than claiming a clean-room origin.
The upstream copyright is retained at the top of each of these files.

| Candidate module | Origin responsibilities |
| --- | --- |
| `src/ui_stream/__init__.py` | extraction package surface and runtime exports |
| `src/ui_stream/runtime.py` | streaming runtime buffering, checkpoints, completion ordering |
| `src/ui_stream/types.py` | streaming types and generalized host ports |
| `src/ui_stream/exceptions.py` | generalized streaming exceptions |
| `src/ui_stream/codec.py` | stream event schema and serialization |
| `src/ui_stream/archive.py` | archive integrity and bounded prefix helpers |
| `src/ui_stream/redis.py` | Redis live store and bound publisher |
| `src/ui_stream/reader.py` | Redis live iteration and cursors |
| `src/ui_stream/sse.py` | SSE encoding helpers |

## Newly generated alongside the extraction

- `src/ui_stream/example.py`: domain counter consumer using public ports.
- Every file under `src/ui_stream/demo/`: synthetic SQLite/HTTP/browser demonstration.
- Every file under `tests/`: independent contracts, synthetic fixtures, release checks.
- `tools/`, metadata, documentation, allowlist and packaging configuration: local
  project and release preparation. The MIT text is the standard license text.

Newly generated is a provenance category, not a determination of ownership.
The maintainer authorized public distribution of this extraction and the new
material. Existing upstream ownership is unchanged. The preparation record retains exact source
hashes and the private source baseline separately; those records are not bundled here.

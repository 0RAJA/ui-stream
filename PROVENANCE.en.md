# Provenance

[简体中文](PROVENANCE.md) · [License](docs/license.en.md)

| Category | Files and origin |
| --- | --- |
| Extracted / derived | `__init__`, `runtime`, `types`, `exceptions`, `codec`, `archive`, `redis`, `reader`, `sse` under `src/ui_stream/`, plus private `_json` and `_cursor` helpers extracted from them: streaming modules from BlueKing - Audit Center (bk-audit), including new/generalized extraction work; conservatively treated as derived as a whole |
| New material | `examples/counter.py`, `demos/sqlite/`, tests, benchmarks, packaging tools, illustrated docs, and workflows |
| Vendored third-party source | No libraries, fonts, or images are vendored; metadata declares dependencies |

Derived modules and extracted private helpers retain the original `THL A29 Limited, a Tencent company` copyright. `LICENSE.upstream` preserves the upstream root MIT declaration verbatim, `LICENSE` contains full MIT terms, and `NOTICE` records the extraction.

The maintainer authorized public distribution of the extraction and new material. This inventory does not reassign ownership or claim upstream endorsement/trademark rights. The independent repository excludes parent history, private configuration, and data.

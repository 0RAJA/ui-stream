# Provenance

[简体中文](PROVENANCE.md) · [License](docs/license.en.md)

| Category | Files and origin |
| --- | --- |
| Extracted / derived | `__init__`, `runtime`, `types`, `exceptions`, `codec`, `archive`, `redis`, `reader`, `sse` under `src/ui_stream/`: streaming modules from BlueKing - Audit Center (bk-audit), including new/generalized refactoring work; conservatively treated as derived as a whole |
| New material | `examples/counter.py`, `demos/sqlite/`, independent tests, benchmarks, packaging tools, illustrated docs, and workflows |
| Vendored third-party source | No libraries, fonts, or images are vendored; metadata declares dependencies |

Nine derived modules retain the original `THL A29 Limited, a Tencent company` copyright. `LICENSE.upstream` preserves the root MIT declaration verbatim, `LICENSE` contains full MIT terms, and `NOTICE` records the extraction.
The maintainer authorized public distribution of the extraction and new material. This technical inventory does not reassign ownership or claim upstream endorsement/trademark rights.
The independent repository imports reviewed files without parent history, configuration, environments, data, or internal handoff materials. Private verification records are not bundled.

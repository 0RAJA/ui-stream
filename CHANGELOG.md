# Changelog

## 0.1.0a1 — initial alpha

The first alpha release; check GitHub Releases and PyPI for publication status.

- Extract a bounded synchronous runtime with generic codec, live publisher and archive
  ports, host-owned atomic completion, immutable summary and optional JSON/Redis profile.
- Include a standard-library counter and durable SQLite/browser Demo with synthetic
  normal/failure scenarios and latest-snapshot reconnect behavior.
- Add standalone contracts, source provenance, MIT/upstream notices, explicit release
  allowlist, local builds and conservative sensitive-content checks.

Future release versions must be unique on the target index. Alpha API changes may be
incompatible; keep exact consumer pins until a stable compatibility policy is declared.

- Add source/wheel/sdist validation CI and a Release artifact workflow; PyPI OIDC
  publishing requires an explicitly approved publisher/environment configuration.

# Changelog

[简体中文](CHANGELOG.md)

## 0.1.0a4 — Write integrity and integration contracts

- **Breaking:** Rename `finish_retry()` to `close_for_retry()` without an alias. It only attempts a checkpoint before closing; it neither retries automatically nor commits the final result.
- **Breaking:** `StreamPolicy` accepts only non-negative built-in int values at construction. Reject bools, int subclasses, floats, strings, and negative values while preserving valid zero semantics.
- **Breaking:** With `state_check`, `iter_live` checks state on its first iteration before any live read. Terminal hints still drain the tail first.
- Reject same-instance write reentry. Escaping reentry after a possible write records DEGRADED for later commits; same-thread read-only callbacks remain supported.
- Export Runtime assembly types at the top level, unify English diagnostic logs, and clarify errors, activity refresh, synchronous cancellation, and bilingual usage contracts.

## 0.1.0a3 — Stream lifecycle and archive helpers

- Add required `EventCodec.reset(reason)` and previous-publisher notification from the new Runtime; transport failure returns False and degrades while rotation transactions stay with the host.
- Deduplicate host identities before existing capacity fitting, reporting appended, duplicate, and capacity-dropped records separately without generated IDs or body deduplication.
- Add synchronous SSEBody with injected encoders and iterator cleanup. SQLite Demo uses its own profile; the SSE module no longer requires Pydantic.
- Add quality, code-security, dependency-audit gates and live status badges; retain upstream license notices and document APIs and migration in both languages.

## 0.1.0a2 — Dynamic policies and integration guides

- `policy` accepts a no-argument `StreamPolicy` factory; each `send` takes one snapshot under its lock, with configuration changes effective on the next send.
- Fixed policies and the read-only property remain compatible; factory errors precede business processing and writes, and close paths do not invoke factories.
- Add dynamic-limit, activity-refresh, error, and close-path regressions; clarify core Chinese comments, bilingual contracts, and performance guidance.

## 0.1.0a1 — Initial alpha

- Three-port synchronous Runtime: bounded buffering, checkpoints, host-atomic results, frozen summary.
- Optional JSON/Redis profiles, standard-library Counter, and SQLite/SSE browser Demo.
- Source, wheel/sdist, README, and sensitive-content CI; Release verification and a separate publishing template.
- Bilingual illustrated docs and reproducible bounded performance benchmarks.

Alpha APIs may change; pin versions and validate adapter contracts before upgrades. GitHub Releases / PyPI provide the actual publication status.

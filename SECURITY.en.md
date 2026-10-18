# Security

[简体中文](SECURITY.md) · [Documentation](docs/README.en.md)

Prefer [GitHub private vulnerability reporting](https://github.com/0RAJA/ui-stream/security/advisories/new). If unavailable, contact maintainer `raja <rajawwang@foxmail.com>`.
Include versions, impact, and a minimal synthetic reproduction. Do not post exploit details, credentials, personal data, or private logs in public issues.
This volunteer project promises no response SLA. Only the latest alpha is supported; earlier alphas receive no guaranteed security backports.

| Boundary | Responsibility |
| --- | --- |
| Host | Permissions, cross-process fencing, actual atomic transactions, retries/restarts, retention |
| Runtime | Process-local serialization; accepted events/nested values remain immutable; configure recoverable errors narrowly |
| Demo | Loopback and synthetic data only, no authentication; dedicated SQLite; restart interrupts rather than resumes computation |
| Release checks | Patterns and allowlists do not prove the absence of every secret, PII item, or undisclosed sensitive meaning |

Do not expose the Demo publicly or copy account credentials into source, packages, or Actions secrets.

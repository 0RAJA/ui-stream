# Security policy

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/0RAJA/ui-stream/security/advisories/new)
to contact the maintainer, [raja (@0RAJA)](https://github.com/0RAJA). Do not post exploit
details, credentials, personal data or private logs in public issues. Include affected
versions, impact and a minimal synthetic reproduction. This volunteer project makes
no response-time SLA; no private email address is published.

Only the latest alpha release is supported. There is no promise of security backports
to earlier alpha versions. Report against the latest available release or main commit
and identify the exact version in your private report.

## Trust boundaries

The host enforces authentication, authorization, execution fencing, atomic commit,
retries, restarts and retention. Runtime locking is process-local. Do not mutate
accepted events or nested values. Configure recoverable exceptions narrowly.

The Demo binds localhost, uses synthetic data and has no authentication. Do not expose
it through a public proxy or use it as a production task API. Use a dedicated Demo
database; startup interrupts unfinished jobs without resuming their computation.

Release checks scan allowlisted files and actual archives. Pattern scanning cannot
prove the absence of every credential, personal datum or undisclosed business meaning.
No dependency source, private account data or credentials are bundled.

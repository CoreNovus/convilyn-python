"""CLI exit code constants — pinned so AI agents can branch on the value.

Pattern mirrors gh / stripe / wrangler:

* 0   — success
* 1   — usage error (missing arg, bad flag, file not found)
* 2   — API / transport failure (auth, 4xx, 5xx after retries)
* 3   — domain failure (e.g. a conversion job finished with status=failed)
* 130 — interrupted by SIGINT (POSIX convention)

Kept in a dedicated module so test suites can import the constants
instead of literal numbers — a script that asserts ``result.exit_code
== EXIT_API_ERROR`` keeps reading right when (if) we shift the codes
later.
"""

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_API_ERROR = 2
EXIT_JOB_FAILED = 3
EXIT_INTERRUPTED = 130

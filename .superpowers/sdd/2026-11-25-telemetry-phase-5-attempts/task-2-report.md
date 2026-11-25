# Task 2 report: execution attempt repository

Implemented the immutable `AttemptRecord` and repository operations in `src/db/repos/execution_attempts.py`.

- `open_for_claim` assigns `coalesce(max(ordinal), 0) + 1`, persists the exact start, lease, and provenance values, and leaves transaction ownership with the caller. Insert failures are isolated in a savepoint and translated through the existing repository error helper.
- `close_open` uses the id, execution id, and open-row guard, and changes only outcome fields; the captured lease remains intact.
- Added history lookup and paginated history/operational queries. Execution history orders by ordinal then id; operational history orders by start time descending then id descending. Both enforce page bounds.
- Added PostgreSQL integration coverage for ordinal independence from legacy delivery count, record immutability, exact values, close idempotence, rollback after open and close, insert failure recovery, execution isolation, filtering, deterministic ordering, page boundaries, counts, and invalid pagination bounds.

## Verification

- RED: initial focused test collection failed because `src.db.repos.execution_attempts` did not exist.
- Focused PostgreSQL integration suite: **10 passed**.
- Ruff check: **passed**.
- Ruff format: **passed** (no formatting changes remaining).
- Pyright on repository module: **0 errors, 0 warnings**.
- Pytest emitted existing dependency/configuration deprecation and future warnings; no test failures.

Database URL used for both `DATABASE_URL` and `TEST_DATABASE_URL`: `postgresql://composer:composer_test@localhost:55433/composer_test`.

# BharatBench implementation status

## Audit findings

- The experiment runner previously used one `AsyncSession` concurrently from
  every worker and discarded exceptions returned by `asyncio.gather`.
- Background execution rolled back the session on failure, losing failure
  state and progress events.
- Cancellation did not stop workers or protect the terminal status.
- Analytics mixed automatic and human evaluations and filtered pending bias
  annotations before calculating coverage.
- Startup always called `create_all`, and configuration exposed development
  credentials as defaults.
- No frontend or migration history existed.

## Completed in this iteration

- Added secure environment validation and `.env.example`.
- Added Alembic configuration and an initial schema migration.
- Added a development-only `AUTO_CREATE_TABLES` compatibility switch.
- Refactored experiment execution to keep external calls separate from
  database transactions, account for worker failures, and preserve cancellation.
- Fixed background failure persistence and duplicate launch protection.
- Separated automated and human analytics denominators and included pending
  bias reviews.

## Validation

Run from the repository root:

```bash
cd backend
python -m compileall app
pytest
```

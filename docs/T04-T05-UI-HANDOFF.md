# T04/T05 UI handoff

## Scope and entry points

- Calendar appointment detail links to `/appointments/:appointmentId/visit` and the patient record.
- The visit view loads the appointment and visit summary directly after route reload. It advances appointment status one accepted step at a time, creates procedures from the existing active catalog, shows server price and duration values, and exposes only the permitted start/pause/resume/complete/cancel actions.
- Completing a procedure leaves the appointment open. The separate close action uses `can_close_visit`; pending/running/paused procedures disable it, and a server conflict remains visible.
- Clinical roles (`doctor`, `clinic_manager`, `administrator`) see the patient record, alerts, visits/procedures, notes/findings, revisions, and the paginated server timeline. Drafts can be reopened only by their author. Reception and assistants do not load the clinical record or see procedure details.

## Local host startup

Use only fictitious records and the temporary SQLite database below. Run each command group in its own terminal from this worktree. The SQLite file is demo-only and is not PostgreSQL validation.

API terminal:

```bash
cd /home/raul/Projects/worktrees/aplica-ie-t04-t05-ui/backend
if [ ! -x .venv/bin/python ]; then python3.12 -m venv .venv; fi
if [ ! -x .venv/bin/uvicorn ]; then .venv/bin/python -m pip install -e .; fi
export DATABASE_URL=sqlite+aiosqlite:////tmp/dentacare-t04-t05-ui-demo.db
export ENVIRONMENT=development
export JWT_SECRET=local-ui-test-secret-change-before-sharing
export CORS_ORIGINS=http://127.0.0.1:5178
if [ -e /tmp/dentacare-t04-t05-ui-demo.db ]; then
  echo 'Database file exists; it will be preserved and only missing tables will be created.'
else
  echo 'Creating the demo database from the SQLAlchemy models.'
fi
.venv/bin/python - <<'PY'
import asyncio

from app.database import Base, engine


async def initialize_demo_schema():
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection, checkfirst=True
            )
        )
    await engine.dispose()


asyncio.run(initialize_demo_schema())
print(f"Initialized {len(Base.metadata.tables)} mapped SQLAlchemy tables.")
PY
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010
```

`Base.metadata.create_all(checkfirst=True)` creates missing tables from the app models and leaves existing tables and rows in place; it does not run Alembic, drop tables, or alter existing columns. If this path already holds a schema from a different demo, keep it and choose another unused filename for a separate demo database. This bootstrap does not validate the PostgreSQL migrations or PostgreSQL behavior. The repository migrations use PostgreSQL-specific DDL such as `JSONB` and `::jsonb` defaults, so do not run `alembic upgrade head` against SQLite.

Frontend terminal:

```bash
cd /home/raul/Projects/worktrees/aplica-ie-t04-t05-ui/frontend
if [ ! -d node_modules ]; then PATH="/home/raul/Projects/dentacare-tools/node/bin:$PATH" npm ci; fi
PATH="/home/raul/Projects/dentacare-tools/node/bin:$PATH" VITE_API_URL=http://127.0.0.1:8010/api npm run dev -- --host 127.0.0.1 --port 5178 --strictPort
```

Open `http://127.0.0.1:5178`. Keep the API CORS origin exactly `http://127.0.0.1:5178`.

## Fictitious setup and browser scenario

1. Register a throwaway clinic administrator. Set its clinic time zone to `Europe/Bucharest` and create one active room.
2. Add one active catalog item, for example `UI-EXAM`, “UI examination”, `30` minutes, base price string `125.50`, currency `RON`.
3. Invite and accept a fictitious doctor account. Sign in as that doctor and create a fictitious patient with no real contact or medical data.
4. Create a future appointment for that patient with the invited doctor and active room. From the calendar, confirm, mark arrived, then open the visit and mark in progress.
5. Add the catalog procedure with FDI `26`, surface `occlusal,mesial`, and a short fictitious note. Start, pause, resume, and complete it. Reload the visit and confirm the server status, duration, and price snapshot remain visible while the appointment remains in progress.
6. Add a second pending procedure and verify the UI prevents closing. Resolve it explicitly by completion or cancellation, then close the appointment as a separate step.
7. Open the patient record from the visit. Create and save a finding draft with a fictitious content string, reload, reopen and edit it, then finalize it. Confirm the finalized record is read-only and its revisions are readable.
8. Open another fictitious patient and confirm no note form or content from the first patient is carried over. Timeline entries are displayed from the persisted server projection; the UI does not call the manager-only timeline refresh endpoint.

## Verification in this worktree

- `frontend`: `npm test -- --reporter=verbose` — 27 passed, 6 files.
- `frontend`: `npm run build` — passed with Node 22.
- `git diff --check` — passed.
- SQLite compilation of the SQLAlchemy model DDL succeeded for all 19 mapped tables. Running the documented `Base.metadata.create_all(checkfirst=True)` bootstrap then timed out after 12 seconds (exit 124) in this sandbox; the new demo file `/tmp/dentacare-t04-t05-ui-demo.db` remains 0 bytes. The command is retained for the host; it never calls Alembic and does not delete data.
- The earlier `aiosqlite.connect(':memory:')` probe raised `TimeoutError` under a two-second `asyncio.wait_for`. The old SQLite Alembic attempt was interrupted after about 25 seconds; Alembic is removed from the demo instructions because project migrations use PostgreSQL-only DDL (`JSONB`, `::jsonb` defaults).
- Uvicorn starts and prints `Uvicorn running on http://127.0.0.1:8010`, but the earlier separate curl failed: `curl: (7) Failed to connect to 127.0.0.1 port 8010 after 0 ms: Could not connect to server`. A new five-second Uvicorn start also reached its listening message, then exited cleanly when the timeout sent shutdown. No API browser flow was completed.
- The frontend dev server cannot bind: `Error: listen EPERM: operation not permitted 127.0.0.1:5178`. A direct loopback socket probe raised `PermissionError: [Errno 1] Operation not permitted`. No browser E2E or persisted API verdict is claimed from this sandbox.

## Delivery

- Base: `430ebeed97e5eddf30649ff4cda270251a6b375a`.
- Branch: `feat/t04-t05-clinical-ui`.
- Final commit: see the task report at `/home/raul/Projects/dentacare-context/reports/t04-t05-ui/LATEST.md`.
- The sandbox could not publish the branch because DNS lookup for `github.com` failed. The branch is included in the CI push filter, so a host-side push will trigger the existing jobs. No PR or candidate CI run exists yet.
- Host publish command: `git -C /home/raul/Projects/worktrees/aplica-ie-t04-t05-ui push -u origin feat/t04-t05-clinical-ui`.
- Local checks: 27 frontend tests across 6 files and frontend build passed. Browser/API persistence remains unverified; see the blocker above.
- This is the UI implementation stage only; full T04/T05 acceptance remains open.

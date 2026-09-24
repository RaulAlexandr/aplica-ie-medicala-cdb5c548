# DentaCare

DentaCare is a multi-tenant dental clinic management platform. This repository contains a FastAPI/async SQLAlchemy backend, PostgreSQL/Alembic migrations, and a React/Vite staff frontend.

## Implemented in this milestone

The API provides clinic registration, JWT access tokens, hashed and rotating refresh tokens, server-side logout/revocation, password hashing with Argon2, UUID identifiers, timezone-aware timestamps, clinic tenant isolation, explicit role authorization, patients with medical fields, partial patient updates, access-controlled patient revision history, search/pagination, patient totals, rooms, and appointments.

Patient PATCH requests use a dedicated partial schema: omitted fields are unchanged, nullable fields may explicitly be cleared, and required identity fields reject explicit null. Clinically significant changes record actor, timestamp, field, previous value, and new value in `patient_revisions`.

Appointments validate clinic ownership for every referenced resource, enforce role policy, validate status transitions, reject reactivation conflicts, and use PostgreSQL GiST exclusion constraints for doctor, room, and assigned-assistant overlap protection under concurrent requests. Intervals are half-open, so a booking ending at the exact start of another booking is allowed.

The staff UI includes login/registration, session refresh and sign-out, overview statistics using a server-side patient count, patient search/list, patient creation, patient detail/edit, and appointment creation/list workflows with loading, empty, validation, and server-error states.

## Role policy

| Role | Patient list/detail | Patient edit/history | Appointments | Room administration |
|---|---|---|---|---|
| `clinic_manager` | Yes | Yes | Create, view, transition | Create, view |
| `administrator` | Yes | Yes | Create, view, transition | Create, view |
| `doctor` | Yes | Yes | Create, view, transition | View |
| `assistant` | Yes | No | View | View |
| `reception` | Yes | No | Create, view, transition | View |

There is no patient-portal role in staff authorization. Tenant identity is derived from the verified access token and database user; request-supplied `clinic_id` is never trusted.

## Local setup

Requirements: Python 3.12, Node.js 22, and PostgreSQL 16. Docker Compose is the easiest local PostgreSQL setup.

```bash
docker compose up -d
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Set JWT_SECRET to a long random value and ENVIRONMENT=development for local work.
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

The staff UI is available at `http://localhost:5173`; the API health check is `http://localhost:8000/health`.

## Environment variables

`DATABASE_URL` must use the async SQLAlchemy driver, normally `postgresql+asyncpg://...`. `JWT_SECRET` is required to be a non-placeholder secret whenever `ENVIRONMENT` is not `development` or `test`. `ACCESS_TOKEN_MINUTES`, `REFRESH_TOKEN_DAYS`, and `CORS_ORIGINS` control token lifetimes and browser origins. Secrets belong in an untracked `.env` file or deployment secret store and must not be committed.

## Migrations

Application startup does not call `create_all` or mutate the schema. Apply migrations with `alembic upgrade head`. Revision `0002_integrity_and_history` adds patient revision history, aligned indexes, the missing Alembic template, and PostgreSQL scheduling guards. It enables `btree_gist` and adds exclusion constraints for doctor, room, and non-null assigned-assistant time ranges. Existing deployments must run the normal upgrade path; tables are not dropped or recreated.

A subsequent revision can be generated with:

```bash
alembic revision -m "describe the change"
```

The PostgreSQL-specific migration and concurrency behavior must be checked against a real PostgreSQL database. SQLite is used only for the fast API unit tests and cannot prove exclusion-constraint behavior.

## Verification

From the repository root after installing dependencies:

```bash
. .venv/bin/activate
cd backend
pytest -q
ruff check app tests
python -m compileall -q app alembic
alembic heads
cd ../frontend
npm run build
```

The implementation was verified with five backend tests, Ruff, Python compilation, Alembic head inspection, and a successful frontend production build. A real empty-PostgreSQL migration run and concurrent PostgreSQL booking test remain environment-dependent and were not run in the sandbox because Docker was unavailable.

## Remaining limitations

Treatment plans, performed-procedure time tracking, odontogram history, six-site periodontology, inventory movements, staff administration, messaging, reporting, the separate patient portal, and future AI service interfaces remain outside this milestone. No fake endpoints claim those modules are complete. The appointment form currently requires the authenticated staff user to supply a doctor UUID; a doctor directory selector is a follow-up UI improvement.

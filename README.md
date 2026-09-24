# DentaCare

DentaCare is a multi-tenant dental clinic management platform. This repository contains the first working milestone: a FastAPI/async SQLAlchemy backend backed by PostgreSQL migrations and a React/Vite staff frontend.

## Completed in milestone 1

The API implements clinic registration, JWT access tokens, rotating hashed refresh tokens, password hashing with Argon2, role checks, UUID identifiers, timezone-aware timestamps, clinic tenant isolation, audit events, patients with medical fields and search/pagination, rooms, and appointments. Appointment creation validates that the patient, doctor, assistant, and room belong to the authenticated clinic and rejects overlapping doctor, room, or explicitly assigned assistant bookings. The frontend provides login/registration, protected staff navigation, clinic overview, patient creation/search/listing, and appointment listing with loading, empty, and error states.

Tables are created only by Alembic; application startup does not call `create_all` or otherwise mutate schema.

## Pending by design

Treatment plans, performed-procedure time tracking, odontogram history, six-site periodontology, inventory movements, staff administration, messaging, reports, the separate patient portal, and future AI service interfaces are not implemented in this milestone. They must be added with append-only clinical history and the domain rules in the benchmark prompt; no fake endpoints are presented as complete.

## Local setup

Requirements: Python 3.12, Node.js 22, Docker, and Docker Compose.

```bash
docker compose up -d
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

The staff UI is at `http://localhost:5173`; the API health check is `http://localhost:8000/health`.

For an isolated test run without PostgreSQL, the tests override the database dependency with in-memory SQLite:

```bash
cd backend
pytest -q
```

## Validation

The repository validation is run by the implementation task and is recorded in the delivery report. It includes the backend integration tests, Alembic import/configuration checks, and `npm run build`.

## Security and data hygiene

Secrets are supplied through environment variables. `.env`, local databases, caches, build output, and Node dependencies are ignored. No patient data or credentials are committed. The development JWT fallback must be replaced through `JWT_SECRET` outside local development.

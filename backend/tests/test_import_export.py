import json
from datetime import date
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.database import AuditEvent, Base, ImportRun, Patient, PatientRevision, User, get_db
from app.import_export.csv_tools import decode_csv
from app.main import app


def test_csv_detects_semicolon_bom_and_keeps_diacritics():
    delimiter, rows = decode_csv("\ufeffNume;Prenume\nȘerban;Ana-Maria\n".encode())
    assert delimiter == ";"
    assert rows == [{"Nume": "Șerban", "Prenume": "Ana-Maria"}]


def test_csv_detects_comma_without_bom():
    delimiter, rows = decode_csv(b"last_name,first_name\nPopescu,Ion\n")
    assert delimiter == ","
    assert rows == [{"last_name": "Popescu", "first_name": "Ion"}]


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions
    app.dependency_overrides.clear()
    await engine.dispose()


async def register(client, email):
    response = await client.post(
        "/api/auth/register",
        json={
            "clinic_name": "Clinica CSV",
            "full_name": "Manager CSV",
            "email": email,
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_patient_preview_is_read_only_and_confirm_is_idempotent(env):
    client, sessions = env
    auth = await register(client, "csv-manager@example.com")
    content = "Nume;Prenume;Data nașterii;Telefon;Email;CNP\nȘerban;Ana;12.02.1980;0712345678;ana@example.com;1800212123456\nBad;Row;31.02.2000;;bad-email;\n".encode(
        "utf-8-sig"
    )
    columns = {
        "last_name": "Nume",
        "first_name": "Prenume",
        "date_of_birth": "Data nașterii",
        "phone": "Telefon",
        "email": "Email",
        "cnp": "CNP",
    }
    response = await client.post(
        "/api/import-export/patients/preview",
        params={"columns": json.dumps(columns)},
        headers={**auth, "X-Filename": "pacienti.csv"},
        content=content,
    )
    assert response.status_code == 200, response.text
    assert (response.json()["new"], response.json()["errors"]) == (1, 1)
    assert response.json()["rows"][1]["message"] == "Dată invalidă"
    async with sessions() as session:
        assert await session.scalar(select(func.count(Patient.id))) == 0
        assert await session.scalar(select(func.count(ImportRun.id))) == 0

    confirm = await client.post(
        "/api/import-export/patients/confirm",
        params={"columns": json.dumps(columns)},
        headers={**auth, "X-Filename": "pacienti.csv"},
        content=content,
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["created"] == 1
    second = await client.post(
        "/api/import-export/patients/confirm",
        params={"columns": json.dumps(columns)},
        headers={**auth, "X-Filename": "pacienti.csv"},
        content=content,
    )
    assert second.status_code == 200 and second.json()["id"] == confirm.json()["id"]
    async with sessions() as session:
        patient = await session.scalar(select(Patient))
        assert (patient.first_name, patient.last_name, patient.cnp) == (
            "Ana",
            "Șerban",
            "1800212123456",
        )
        assert await session.scalar(select(func.count(Patient.id))) == 1


@pytest.mark.asyncio
async def test_import_run_is_clinic_scoped_and_export_has_excel_bom(env):
    client, sessions = env
    first = await register(client, "csv-manager-a@example.com")
    second = await register(client, "csv-manager-b@example.com")
    content = b"last_name,first_name\nPopescu,Ion\n"
    columns = json.dumps({"last_name": "last_name", "first_name": "first_name"})
    run = await client.post(
        "/api/import-export/patients/confirm",
        params={"columns": columns},
        headers={**first, "X-Filename": "p.csv"},
        content=content,
    )
    assert run.status_code == 200
    me = (await client.get("/api/auth/me", headers=first)).json()
    assistant = User(
        id=UUID("00000000-0000-0000-0000-000000000111"),
        clinic_id=UUID(me["clinic_id"]),
        email="csv-assistant@example.com",
        password_hash="unused",
        full_name="Asistent CSV",
        role="assistant",
        is_active=True,
    )
    async with sessions() as session:
        session.add(assistant)
        await session.commit()
    denied = await client.get(
        "/api/import-export/runs",
        headers={"Authorization": f"Bearer {create_access_token(assistant)}"},
    )
    assert denied.status_code == 403
    hidden = await client.get(f"/api/import-export/runs/{run.json()['id']}", headers=second)
    assert hidden.status_code == 404
    exported = await client.get(
        "/api/import-export/export/patients",
        params={"start": "2020-01-01", "end": "2030-01-01"},
        headers=first,
    )
    assert exported.status_code == 200
    assert exported.content.startswith(b"\xef\xbb\xbf")
    assert ";" in exported.content.decode("utf-8-sig").splitlines()[0]
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.action == "exported_patients")
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.entity_type == "import_run")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_appointment_preview_reports_unknown_doctor_and_conflict(env):
    client, _ = env
    auth = await register(client, "csv-appointments@example.com")
    patient = await client.post(
        "/api/patients", headers=auth, json={"first_name": "Ana", "last_name": "Pop"}
    )
    room = await client.post("/api/rooms", headers=auth, json={"name": "Cabinet CSV"})
    assert patient.status_code == 201 and room.status_code == 201
    headers = "Prenume pacient,Nume pacient,Medic,Cabinet,Data,Ora,Durata,Tip\n"
    row = "Ana,Pop,Manager CSV,Cabinet CSV,2027-01-11,10:00,30,Control\n"
    content = (headers + row + row).encode()
    columns = {
        "patient_first_name": "Prenume pacient",
        "patient_last_name": "Nume pacient",
        "doctor": "Medic",
        "room": "Cabinet",
        "date": "Data",
        "time": "Ora",
        "duration_minutes": "Durata",
        "appointment_type": "Tip",
    }
    response = await client.post(
        "/api/import-export/appointments/preview",
        params={"columns": json.dumps(columns)},
        headers={**auth, "X-Filename": "programari.csv"},
        content=content,
    )
    assert response.status_code == 200, response.text
    assert response.json()["new"] == 1
    assert "Conflict" in response.json()["rows"][1]["message"]
    confirmed = await client.post(
        "/api/import-export/appointments/confirm",
        params={"columns": json.dumps(columns)},
        headers={**auth, "X-Filename": "programari.csv"},
        content=content,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created"] == 1
    repeated = await client.post(
        "/api/import-export/appointments/confirm",
        params={"columns": json.dumps(columns)},
        headers={**auth, "X-Filename": "programari.csv"},
        content=content,
    )
    assert repeated.status_code == 200 and repeated.json()["id"] == confirmed.json()["id"]

    unknown = content.replace(b"Manager CSV", b"Medic necunoscut")
    response = await client.post(
        "/api/import-export/appointments/preview",
        params={"columns": json.dumps(columns)},
        headers={**auth, "X-Filename": "programari2.csv"},
        content=unknown,
    )
    assert response.status_code == 200
    assert "Medic necunoscut" in response.json()["rows"][0]["message"]


@pytest.mark.asyncio
async def test_existing_patient_is_only_updated_when_selected_and_creates_revisions(env):
    client, sessions = env
    auth = await register(client, "csv-review@example.com")
    patient = await client.post(
        "/api/patients",
        headers=auth,
        json={"first_name": "Ana", "last_name": "Pop", "phone": "0711111111"},
    )
    assert patient.status_code == 201
    async with sessions() as session:
        stored = await session.get(Patient, UUID(patient.json()["id"]))
        stored.cnp = "1800212123456"
        await session.commit()
    content = b"Nume,Prenume,Telefon,CNP\nPop,Anca,0722222222,1800212123456\n"
    columns = {"last_name": "Nume", "first_name": "Prenume", "phone": "Telefon", "cnp": "CNP"}
    preview = await client.post(
        "/api/import-export/patients/preview",
        params={"columns": json.dumps(columns)},
        headers=auth,
        content=content,
    )
    assert preview.status_code == 200
    assert preview.json()["rows"][0]["status"] == "review"
    assert (await client.get(f"/api/patients/{patient.json()['id']}", headers=auth)).json()[
        "first_name"
    ] == "Ana"
    confirmed = await client.post(
        "/api/import-export/patients/confirm",
        params={"columns": json.dumps(columns), "update_rows": "2"},
        headers=auth,
        content=content,
    )
    assert confirmed.status_code == 200 and confirmed.json()["updated"] == 1
    async with sessions() as session:
        stored = await session.get(Patient, UUID(patient.json()["id"]))
        revisions = list(
            (
                await session.scalars(
                    select(PatientRevision).where(PatientRevision.patient_id == stored.id)
                )
            ).all()
        )
        assert (stored.first_name, stored.phone, stored.cnp) == (
            "Anca",
            "0722222222",
            "1800212123456",
        )
        assert {revision.field for revision in revisions} == {"first_name", "phone"}


@pytest.mark.asyncio
async def test_two_thousand_patient_preview_uses_one_patient_query(env):
    client, sessions = env
    auth = await register(client, "csv-batch@example.com")
    content = (
        "Nume,Prenume,Telefon\n"
        + "".join(f"Familia{i},Persoana{i},07{i:08d}\n" for i in range(2000))
    ).encode()
    queries = []
    engine = sessions.kw["bind"]

    def record(_conn, _cursor, statement, *_args, **_kwargs):
        if "FROM patients" in statement and statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        columns = {"last_name": "Nume", "first_name": "Prenume", "phone": "Telefon"}
        response = await client.post(
            "/api/import-export/patients/preview",
            params={"columns": json.dumps(columns)},
            headers=auth,
            content=content,
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2000
    assert len(queries) == 1


@pytest.mark.asyncio
async def test_patient_match_priority_covers_external_id_cnp_and_name_birth_phone(env):
    client, sessions = env
    auth = await register(client, "csv-identities@example.com")
    me = (await client.get("/api/auth/me", headers=auth)).json()
    async with sessions() as session:
        session.add_all(
            [
                Patient(
                    clinic_id=UUID(me["clinic_id"]),
                    first_name="Ana",
                    last_name="Ext",
                    external_id="EXT-1",
                ),
                Patient(
                    clinic_id=UUID(me["clinic_id"]),
                    first_name="Ion",
                    last_name="Cnp",
                    cnp="1800212123456",
                ),
                Patient(
                    clinic_id=UUID(me["clinic_id"]),
                    first_name="Mara",
                    last_name="Fallback",
                    date_of_birth=date(1985, 5, 6),
                    phone="0700000000",
                ),
            ]
        )
        await session.commit()
    content = (
        "Nume,Prenume,Data nașterii,Telefon,CNP,Identificator\n"
        "Schimbat,Nume,,, ,EXT-1\n"
        "Schimbat,Nume,,,1800212123456,\n"
        "Fallback,Mara,1985-05-06,0700000000,,\n"
    ).encode()
    columns = {
        "last_name": "Nume",
        "first_name": "Prenume",
        "date_of_birth": "Data nașterii",
        "phone": "Telefon",
        "cnp": "CNP",
        "external_id": "Identificator",
    }
    response = await client.post(
        "/api/import-export/patients/preview",
        params={"columns": json.dumps(columns)},
        headers=auth,
        content=content,
    )
    assert response.status_code == 200, response.text
    assert [row["status"] for row in response.json()["rows"]] == ["review", "review", "exists"]


@pytest.mark.asyncio
async def test_patient_validation_reports_email_and_required_field_errors(env):
    client, _ = env
    auth = await register(client, "csv-validation@example.com")
    content = b"Nume,Prenume,Email\nPop,Ana,not-an-email\n,,valid@example.com\n"
    columns = {"last_name": "Nume", "first_name": "Prenume", "email": "Email"}
    response = await client.post(
        "/api/import-export/patients/preview",
        params={"columns": json.dumps(columns)},
        headers=auth,
        content=content,
    )
    assert response.status_code == 200
    assert [row["message"] for row in response.json()["rows"]] == [
        "Email invalid",
        "Câmp obligatoriu lipsă: nume sau prenume",
    ]

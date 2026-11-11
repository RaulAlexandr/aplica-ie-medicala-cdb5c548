#!/usr/bin/env python3
"""Create fictional pilot data; invitations use the application HTTP API."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

if (
    os.environ.get("PILOT_MODE") != "1"
    or os.environ.get("ENVIRONMENT") != "development"
):
    raise SystemExit(
        "Refuz seed: setează PILOT_MODE=1 și ENVIRONMENT=development numai pentru baza fictivă."
    )

from app import main as _app_main  # noqa: F401 - load registered models/routers
from app.auth.service import authenticate, create_access_token
from app.database import (
    Appointment,
    Base,
    Clinic,
    Patient,
    ProcedureCatalog,
    Room,
    SessionLocal,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kw):
    return "JSON"


api = os.environ["PILOT_API_URL"]
root = Path(os.environ["PILOT_ROOT"])
credentials_path = root / "credentials.json"
namespace = UUID("fa7166a6-734e-4055-909f-38d88ac6b18e")
clinic_name = "Clinica Demo Fictivă"


def stable_id(name: str) -> UUID:
    return uuid5(namespace, name)


def request(
    method: str, path: str, payload: dict | None = None, token: str | None = None
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(api + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"API {method} {path}: HTTP {exc.code}: {detail}") from exc


def load_credentials() -> dict:
    if credentials_path.exists():
        return json.loads(credentials_path.read_text())
    credentials = {
        "clinic": clinic_name,
        "manager": {
            "name": "Mara Demo",
            "email": "manager@example.com",
            "password": secrets.token_urlsafe(18),
        },
        "doctor": {
            "name": "Dr. Alex Demo",
            "email": "doctor@example.com",
            "password": secrets.token_urlsafe(18),
        },
        "reception": {
            "name": "Radu Recepție Demo",
            "email": "reception@example.com",
            "password": secrets.token_urlsafe(18),
        },
    }
    temp = credentials_path.with_suffix(".tmp")
    temp.write_text(json.dumps(credentials, indent=2, ensure_ascii=False) + "\n")
    temp.chmod(0o600)
    temp.replace(credentials_path)
    credentials_path.chmod(0o600)
    return credentials


async def seed_records(clinic_id: UUID, doctor_id: UUID) -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(conn, checkfirst=True)
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    zone = ZoneInfo("Europe/Bucharest")
    today = datetime.now(zone).date()
    async with session_factory() as session:
        clinic = await session.get(Clinic, clinic_id)
        if clinic is None:
            raise RuntimeError("Clinica managerului nu există în baza indicată.")
        clinic.timezone = "Europe/Bucharest"

        for name in ("Cabinet Demo 1", "Cabinet Demo 2"):
            room_id = stable_id("room:" + name)
            if await session.get(Room, room_id) is None:
                session.add(
                    Room(id=room_id, clinic_id=clinic_id, name=name, is_active=True)
                )

        patient_specs = [
            ("pacient:ioana", "Ioana", "Exemplu", "1990-03-12", "0722000001"),
            ("pacient:andrei", "Andrei", "Model", "1985-09-24", "0722000002"),
            ("pacient:elena", "Elena", "Test", "2001-05-06", "0722000003"),
        ]
        patient_ids = []
        for key, first, last, birth, phone in patient_specs:
            patient_id = stable_id(key)
            patient_ids.append(patient_id)
            if await session.get(Patient, patient_id) is None:
                session.add(
                    Patient(
                        id=patient_id,
                        clinic_id=clinic_id,
                        first_name=first,
                        last_name=last,
                        date_of_birth=date.fromisoformat(birth),
                        phone=phone,
                        email=None,
                        notes="Date demonstrative fictive.",
                    )
                )

        catalog_specs = [
            ("CONS-DEMO", "Consultație demonstrativă", 30, "150.00"),
            ("PROF-DEMO", "Igienizare demonstrativă", 45, "250.00"),
            ("REST-DEMO", "Obturație demonstrativă", 60, "400.00"),
        ]
        for code, name, duration, price in catalog_specs:
            item_id = stable_id("procedure:" + code)
            if await session.get(ProcedureCatalog, item_id) is None:
                session.add(
                    ProcedureCatalog(
                        id=item_id,
                        clinic_id=clinic_id,
                        code=code,
                        code_lower=code.lower(),
                        name=name,
                        description="Înregistrare fictivă pentru pilot.",
                        category="Demo",
                        default_duration_minutes=duration,
                        base_price=Decimal(price),
                        currency="RON",
                        is_active=True,
                    )
                )

        room_ids = [stable_id("room:Cabinet Demo 1"), stable_id("room:Cabinet Demo 2")]
        upcoming_weekdays = [
            (today + timedelta(days=offset), hour)
            for offset, hour in enumerate((10, 12, 9, 11, 14, 10, 12), start=1)
            if (today + timedelta(days=offset)).weekday() < 5
        ][:3]
        for index, (appointment_day, hour) in enumerate(upcoming_weekdays):
            appointment_id = stable_id(f"appointment:{index + 1}")
            if await session.get(Appointment, appointment_id) is None:
                local_start = datetime.combine(
                    appointment_day, time(hour, 0), tzinfo=zone
                ).astimezone(UTC)
                session.add(
                    Appointment(
                        id=appointment_id,
                        clinic_id=clinic_id,
                        patient_id=patient_ids[index],
                        doctor_id=doctor_id,
                        assistant_id=None,
                        room_id=room_ids[index % 2],
                        starts_at=local_start,
                        ends_at=local_start + timedelta(minutes=45),
                        duration_minutes=45,
                        status="scheduled",
                        appointment_type=catalog_specs[index][1],
                        notes="Programare fictivă pentru testarea pilotului.",
                    )
                )
        await session.commit()
    await engine.dispose()


async def manager_access_token(email: str, password: str) -> str:
    async with SessionLocal() as session:
        user = await authenticate(session, email, password)
        if user is None or user.role != "clinic_manager":
            raise RuntimeError(
                "Autentificarea managerului demo a eșuat; baza/fișierul de cont pot să nu corespundă."
            )
        return create_access_token(user)


async def ensure_schema() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(conn, checkfirst=True)
        )
    await engine.dispose()


def main() -> None:
    asyncio.run(ensure_schema())
    credentials = load_credentials()
    manager = credentials["manager"]
    # Register the clinic manager once; subsequent runs authenticate the existing account.
    try:
        auth = request(
            "POST",
            "/api/auth/register",
            {
                "clinic_name": clinic_name,
                "full_name": manager["name"],
                "email": manager["email"],
                "password": manager["password"],
            },
        )
    except RuntimeError as exc:
        if "HTTP 409" not in str(exc):
            raise
        auth = {
            "access_token": asyncio.run(
                manager_access_token(manager["email"], manager["password"])
            )
        }
    token = auth["access_token"]
    roster = request("GET", "/api/staff", token=token)
    staff_by_email = {member["email"].lower(): member for member in roster}
    ids: dict[str, UUID] = {
        "manager": UUID(
            next(
                item["id"]
                for item in roster
                if item["email"].lower() == manager["email"].lower()
            )
        )
    }
    for role_key, role_name in (("doctor", "doctor"), ("reception", "reception")):
        person = credentials[role_key]
        existing = staff_by_email.get(person["email"].lower())
        if existing is None:
            invitation = request(
                "POST",
                "/api/staff/invitations",
                {
                    "email": person["email"],
                    "full_name": person["name"],
                    "role": role_name,
                },
                token=token,
            )
            invite_token = invitation["invitation_url"].split("token=", 1)[1]
            request(
                "POST",
                f"/api/staff/invitations/accept?token={invite_token}",
                {"password": person["password"]},
            )
            roster = request("GET", "/api/staff", token=token)
            staff_by_email = {member["email"].lower(): member for member in roster}
            existing = staff_by_email[person["email"].lower()]
        if existing["role"] != role_name:
            raise RuntimeError(
                f"Rol neașteptat pentru {person['email']}; nu suprascriu contul existent."
            )
        ids[role_key] = UUID(existing["id"])
    clinic_id = UUID(
        auth.get("clinic_id")
        or next(
            member["clinic_id"]
            for member in roster
            if member["email"].lower() == manager["email"].lower()
        )
    )
    asyncio.run(seed_records(clinic_id, ids["doctor"]))
    print(
        f"Seed fictiv OK: {clinic_name}; {len(roster)} membri, 3 pacienți, 2 cabinete, 3 servicii, 3 programări."
    )
    print(
        f"Conturile demo sunt păstrate local în {credentials_path} (permisiuni 600). Nu copia parola/tokenul în Git sau raport."
    )


if __name__ == "__main__":
    main()

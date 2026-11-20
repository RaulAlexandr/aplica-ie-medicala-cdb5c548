#!/usr/bin/env python3
"""Create fictional pilot data; invitations use the application HTTP API."""

from __future__ import annotations

import asyncio
import io
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
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


def request_with_headers(method: str, path: str, payload: dict, token: str, extra: dict) -> dict:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}", **extra}
    req = urllib.request.Request(api + path, data=json.dumps(payload).encode(), headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"API {method} {path}: HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}") from exc


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
        clinical_alerts = {
            "pacient:ioana": {"allergies": "Alergie fictivă la penicilină."},
            "pacient:andrei": {"chronic_diseases": "Diabet zaharat tip 2 (fictiv).", "medical_alerts": "Control glicemic recomandat înainte de proceduri lungi."},
        }
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
                        **clinical_alerts.get(key, {}),
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
        # Today (clinic zone) and the next weekdays; fixed ids make reruns add nothing.
        next_days = [today + timedelta(days=n) for n in range(1, 8) if (today + timedelta(days=n)).weekday() < 5][:2]
        agenda = [
            ("appointment:today-ioana", 0, today, 9, 0, 60, 0, "in_progress", "Obturație demonstrativă", "Vizită în desfășurare: obturație pe 1.6."),
            ("appointment:today-andrei", 1, today, 11, 0, 45, 1, "scheduled", "Consultație demonstrativă", "Consultație de control, fictivă."),
            ("appointment:today-elena", 2, today, 14, 0, 30, 0, "confirmed", "Igienizare demonstrativă", "Pacientă confirmată telefonic (fictiv)."),
            ("appointment:next-ioana", 0, next_days[0], 15, 0, 30, 1, "scheduled", "Control după obturație", "Programare viitoare fictivă."),
            ("appointment:next-andrei", 1, next_days[1], 16, 0, 45, 0, "scheduled", "Reevaluare parodontală", "Programare viitoare fictivă."),
        ]
        for key, patient_index, day, hour, minute, minutes, room_index, status, kind, notes in agenda:
            appointment_id = stable_id(key)
            if await session.get(Appointment, appointment_id) is None:
                start = datetime.combine(day, time(hour, minute), tzinfo=zone).astimezone(UTC)
                session.add(
                    Appointment(
                        id=appointment_id,
                        clinic_id=clinic_id,
                        patient_id=patient_ids[patient_index],
                        doctor_id=doctor_id,
                        assistant_id=None,
                        room_id=room_ids[room_index],
                        starts_at=start,
                        ends_at=start + timedelta(minutes=minutes),
                        duration_minutes=minutes,
                        status=status,
                        appointment_type=kind,
                        notes=notes,
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

# ---------------------------------------------------------------------------
# Clinical demo data (T04-T13) through the real HTTP API, as the real roles.
# Every write is idempotent: fixed Idempotency-Key/client_request_id (uuid5), or a
# lookup by a stable marker before creating. No credential or token is printed.
# ---------------------------------------------------------------------------


def login_token(person: dict) -> str:
    return request("POST", "/api/auth/login", {"email": person["email"], "password": person["password"]})["access_token"]


def request_raw(path: str, body: bytes, headers: dict, token: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        api + path, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {"detail": exc.read().decode(errors="replace")[:300]}


def fictional_images() -> dict[str, bytes]:
    """Real PNG/JPEG files drawn locally with Pillow (a backend dependency); no real data."""
    from PIL import Image, ImageDraw

    def render(size, base, accent, fmt):
        image = Image.new("RGB", size, base)
        draw = ImageDraw.Draw(image)
        width, height = size
        for index in range(8):
            left = 60 + index * (width - 120) // 8
            draw.rounded_rectangle([left, height // 4, left + (width - 160) // 9, height * 3 // 4], radius=18, fill=accent)
        draw.text((20, 14), "DEMO FICTIV - fara date reale", fill=(255, 255, 255))
        buffer = io.BytesIO()
        image.save(buffer, fmt, **({"quality": 85} if fmt == "JPEG" else {}))
        return buffer.getvalue()

    return {
        "radiografie": render((1200, 700), (28, 28, 36), (190, 190, 200), "PNG"),
        "fotografie": render((1000, 750), (196, 150, 120), (240, 236, 230), "JPEG"),
    }


def minimal_pdf() -> bytes:
    return (b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\n"
            b"trailer << /Root 1 0 R >>\n%%EOF\n")


def seed_clinical(credentials: dict, ids: dict[str, UUID], manager_token: str) -> dict:
    doctor = login_token(credentials["doctor"])
    reception = login_token(credentials["reception"])
    zone = ZoneInfo("Europe/Bucharest")
    today = datetime.now(zone).date()
    ioana, andrei, elena = (str(stable_id(f"pacient:{name}")) for name in ("ioana", "andrei", "elena"))
    visit_ioana = str(stable_id("appointment:today-ioana"))
    visit_elena = str(stable_id("appointment:today-elena"))
    catalog = {code: str(stable_id("procedure:" + code)) for code in ("CONS-DEMO", "PROF-DEMO", "REST-DEMO")}
    summary: dict[str, int] = {}

    def count(name: str) -> None:
        summary[name] = summary.get(name, 0) + 1

    # --- T06 odontogram: missing teeth + findings (draft -> finalized) -----------------
    for patient, tooth, status in ((ioana, "18", "missing"), (ioana, "28", "missing"), (andrei, "36", "missing")):
        request("PUT", f"/api/odontogram/patients/{patient}/teeth/{tooth}", {"status": status}, doctor)
    findings = [
        (ioana, "16", "Carie", "Carie ocluzală profundă (date demonstrative).", ["occlusal"]),
        (ioana, "36", "Obturație existentă", "Obturație veche cu margini adaptate (fictiv).", ["mesial", "occlusal"]),
        (ioana, "21", "Fractură", "Fractură de colț incizal, fără sensibilitate (fictiv).", ["incisal"]),
        (andrei, "46", "Carie", "Carie distală incipientă (fictiv).", ["distal"]),
        (andrei, "11", "Recesiune", "Recesiune gingivală vestibulară (fictiv).", ["vestibular"]),
    ]
    for patient, tooth, kind, description, surfaces in findings:
        key = str(stable_id(f"finding:{patient}:{tooth}:{kind}"))
        body = {"toothId": tooth, "kind": kind, "description": description, "surfaces": surfaces}
        created = request_with_headers("POST", f"/api/odontogram/patients/{patient}/findings", body, doctor, {"Idempotency-Key": key})
        if created.get("status") == "draft":
            request("POST", f"/api/odontogram/findings/{created['id']}/finalize", token=doctor)
            count("constatări finalizate")

    # --- T05 clinical note + alert ---------------------------------------------------------
    notes = request("GET", f"/api/clinical-notes/patients/{ioana}", token=doctor)
    if not any(note.get("title") == "Anamneză demonstrativă" for note in notes):
        note = request("POST", "/api/clinical-notes", {
            "patient_id": ioana, "appointment_id": visit_ioana, "title": "Anamneză demonstrativă",
            "content": "Pacienta acuză sensibilitate la rece pe 1.6. Fără dureri spontane. Date fictive.",
            "tooth_fdi": "16",
        }, doctor)
        request("POST", f"/api/clinical-notes/{note['id']}/finalize", token=doctor)
        count("note clinice")

    # --- T04 performed procedure on the open visit (measured timer: start -> complete) ---
    procedures = request("GET", f"/api/performed-procedures?patient_id={ioana}&limit=200", token=doctor)
    done = next((row for row in procedures if row["appointment_id"] == visit_ioana and row["procedure_code"] == "REST-DEMO"
                 and row.get("tooth_fdi") == "16" and row["status"] != "cancelled"), None)
    if done is None:
        done = request("POST", "/api/performed-procedures", {
            "appointment_id": visit_ioana, "procedure_catalog_id": catalog["REST-DEMO"],
            "tooth_fdi": "16", "tooth_surface": "occlusal", "notes": "Obturație pe 1.6 din plan (fictiv).",
        }, doctor)
        count("manopere efectuate")
    if done["status"] == "pending":
        done = request("POST", f"/api/performed-procedures/{done['id']}/start", token=doctor)
    if done["status"] in ("running", "paused"):
        time.sleep(2.2)  # a short but real measured time, so the visit shows a non-zero active duration
        done = request("POST", f"/api/performed-procedures/{done['id']}/complete", token=doctor)

    # --- T08 treatment plan: stages, accepted, one item linked to the performed procedure ---
    plan_title = "Plan restaurativ demonstrativ"
    plans = request("GET", f"/api/treatment-plans/patients/{ioana}", token=doctor)
    existing = next((row for row in plans if row["title"] == plan_title), None)
    if existing is None:
        plan = request("POST", f"/api/treatment-plans/patients/{ioana}", {
            "title": plan_title, "notes": "Plan fictiv în două etape.",
            "stages": [
                {"title": "Etapa 1 — tratament urgent", "items": [{
                    "procedure_catalog_id": catalog["REST-DEMO"], "tooth_fdi": "16", "tooth_surface": "occlusal",
                    "priority": "high", "estimated_price": "400.00", "estimated_duration_minutes": 60}]},
                {"title": "Etapa 2 — igienizare și control", "items": [
                    {"procedure_catalog_id": catalog["PROF-DEMO"], "priority": "normal"},
                    {"procedure_catalog_id": catalog["CONS-DEMO"], "priority": "low"}]},
            ],
        }, doctor)
        count("planuri")
    else:
        plan = request("GET", f"/api/treatment-plans/{existing['id']}", token=doctor)
    if plan["status"] == "draft":
        plan = request("POST", f"/api/treatment-plans/{plan['id']}/accept", {"note": "Plan acceptat de pacientă (fictiv)."}, doctor)
    first_item = plan["stages"][0]["items"][0]
    if first_item.get("performed_procedure") is None:
        request("PUT", f"/api/treatment-plans/items/{first_item['id']}/performed-procedure",
                {"performed_procedure_id": done["id"]}, doctor)
        count("legături plan-manoperă")

    # --- T09/T10 two finalized periodontal assessments (second follows the first) ---------
    assessments = request("GET", f"/api/periodontal/patients/{andrei}/assessments", token=doctor)

    def measurements(depth_shift: int, bop_every: int) -> dict:
        teeth = []
        for tooth_index, tooth in enumerate(("16", "11", "26", "36", "46")):
            sites = []
            for site_index, site in enumerate(("buccal_mesial", "buccal_mid", "buccal_distal", "oral_mesial", "oral_mid", "oral_distal")):
                base = 3 + (tooth_index + site_index) % 4
                sites.append({"site": site, "probing_depth_mm": max(1, base - depth_shift),
                              "recession_mm": (tooth_index + site_index) % 2,
                              "bop": (tooth_index + site_index) % bop_every == 0,
                              "plaque": (site_index % 3 == 0) if depth_shift == 0 else (site_index % 5 == 0),
                              "suppuration": False})
            teeth.append({"tooth_fdi": tooth, "is_excluded": False, "sites": sites})
        return {"teeth": teeth}

    def complete(assessment: dict, data: dict) -> dict:
        # Resume-safe: a draft left by an interrupted run is measured and finalized, never duplicated.
        if assessment["status"] == "draft":
            request("PUT", f"/api/periodontal/assessments/{assessment['id']}/measurements", data, doctor)
            request("POST", f"/api/periodontal/assessments/{assessment['id']}/finalize", token=doctor)
            count("evaluări parodontale")
        return assessment

    ordered = sorted(assessments, key=lambda row: row["clinical_date"])
    first = next((row for row in ordered if row["previous_assessment_id"] is None), None)
    if first is None:
        first = request("POST", f"/api/periodontal/patients/{andrei}/assessments",
                        {"clinical_date": (today - timedelta(days=90)).isoformat()}, doctor)
    complete(first, measurements(0, 2))
    second = next((row for row in ordered if row["previous_assessment_id"] == first["id"]), None)
    if second is None:
        second = request("POST", f"/api/periodontal/patients/{andrei}/assessments", {
            "clinical_date": (today - timedelta(days=2)).isoformat(), "previous_assessment_id": first["id"],
            "relationship_kind": "follow_up", "change_reason": "Reevaluare după igienizare (fictiv)."}, doctor)
    complete(second, measurements(1, 4))

    # --- T11 documents: real PNG/JPEG/PDF generated locally -------------------------------
    images = fictional_images()
    uploads = [
        (elena, "radiograph", "radiografie-36-demo.png", "Radiografie retroalveolară fictivă", images["radiografie"], {"tooth_fdi": "36", "appointment_id": visit_elena}),
        (elena, "photo", "fotografie-intraorala-demo.jpg", "Fotografie intraorală fictivă", images["fotografie"], {}),
        (elena, "document", "consimtamant-demo.pdf", "Consimțământ fictiv (document demonstrativ)", minimal_pdf(), {}),
        (ioana, "radiograph", "radiografie-16-demo.png", "Radiografie înainte de obturație (fictiv)", images["radiografie"], {"tooth_fdi": "16", "appointment_id": visit_ioana}),
    ]
    for patient, category, name, description, data, extra in uploads:
        existing_docs = request("GET", f"/api/documents/patients/{patient}?include_archived=true", token=doctor)
        if any(doc["original_filename"] == name for doc in existing_docs):
            continue
        query = "&".join(f"{k}={v}" for k, v in {"category": category, "clinical_date": today.isoformat(), **extra}.items())
        status, body = request_raw(f"/api/documents/patients/{patient}?{query}", data, {
            "X-File-Name": urllib.parse.quote(name), "X-Document-Description": urllib.parse.quote(description),
            "Idempotency-Key": str(stable_id(f"document:{patient}:{name}")),
            "Content-Type": "application/octet-stream"}, doctor)
        if status not in (200, 201):
            raise RuntimeError(f"Încărcare document demo respinsă (HTTP {status}): {body.get('detail')}")
        count("documente")

    # --- T13 movements: payment on a visit, an advance, a reversal with reason, an adjustment ---
    def entry(patient, token, key, **body):
        return request("POST", f"/api/finance/patients/{patient}/entries", {"client_request_id": str(stable_id("entry:" + key)), **body}, token)

    entry(ioana, reception, "ioana-cash", kind="payment", amount="200.00", method="cash", appointment_id=visit_ioana, reason="Încasare parțială obturație 1.6")
    mistaken = entry(ioana, reception, "ioana-mistaken", kind="payment", amount="50.00", method="card", reason="Plată introdusă din greșeală (fictiv)")
    request("POST", f"/api/finance/entries/{mistaken['id']}/reverse",
            {"client_request_id": str(stable_id("entry:ioana-mistaken-reversal")), "reason": "Anulată: suma a fost introdusă pe pacientul greșit (demo)."}, reception)
    entry(andrei, reception, "andrei-advance", kind="payment", amount="300.00", method="card", reason="Avans pentru tratament planificat")
    entry(elena, manager_token, "elena-adjustment", kind="adjustment_credit", amount="25.00", reason="Reducere de loialitate demonstrativă")
    return summary


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
    created = seed_clinical(credentials, ids, token)
    print(
        f"Seed fictiv OK: {clinic_name}; {len(roster)} membri, 3 pacienți, 2 cabinete, 3 servicii, 8 programări, "
        "date clinice T04–T13 (odontogramă, plan, parodontal, documente, încasări)."
    )
    print("Adăugat în această rulare: " + (", ".join(f"{v} {k}" for k, v in created.items()) or "nimic (deja existent)."))
    print(
        f"Conturile demo sunt păstrate local în {credentials_path} (permisiuni 600). Nu copia parola/tokenul în Git sau raport."
    )


if __name__ == "__main__":
    main()

from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from sqlalchemy import update

from app.appointment_procedures.router import router as appointment_procedures_router
from app.appointments.router import availability_router, staff_router
from app.appointments.router import router as appointments_router
from app.auth.router import router as auth_router
from app.automations.router import router as automations_router
from app.cabinet_ready.router import router as cabinet_ready_router
from app.clinic.router import router as clinic_router
from app.clinical_notes.router import router as clinical_notes_router
from app.collab.router import router as collab_router
from app.comms.router import router as comms_router
from app.config import settings
from app.database import (
    AccessLog,
    Appointment,
    ClinicalDocument,
    ClinicalNote,
    PerformedProcedure,
    PeriodontalAssessment,
    RefreshToken,
    SessionLocal,
    TreatmentPlan,
    User,
    get_db,
    utcnow,
)
from app.documents.router import router as documents_router
from app.equipment.router import router as equipment_router
from app.finance.router import router as finance_router
from app.forms.router import router as forms_router
from app.inventory.router import router as inventory_router
from app.materials.router import router as materials_router
from app.odontogram.router import router as odontogram_router
from app.patients.router import router as patients_router
from app.performed_procedures.router import router as performed_procedures_router
from app.periodontal.router import router as periodontal_router
from app.procedures.router import router as procedures_router
from app.reports.router import router as reports_router
from app.rooms_router import router as rooms_router
from app.security.router import router as security_router
from app.staff.router import router as staff_management_router
from app.sterilization.router import router as sterilization_router
from app.team.router import router as team_router
from app.treatment_plans.router import router as treatment_plans_router
from app.waitlist.router import router as waitlist_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="DentaCare API", version="0.1.0", lifespan=lifespan)


@asynccontextmanager
async def _audit_session():
    override = app.dependency_overrides.get(get_db)
    if override:
        generator = override()
        session = await anext(generator)
        try:
            yield session
        finally:
            await generator.aclose()
    else:
        async with SessionLocal() as session:
            yield session
app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_and_patient_access(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if (request.url.path.startswith("/api/")
            and request.headers.get("authorization", "").lower().startswith("bearer ")
            and "no-store" not in response.headers.get("Cache-Control", "").lower()):
        response.headers["Cache-Control"] = "no-store"
    if settings.https_enabled:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.method == "GET" and response.status_code < 400:
        parts = request.url.path.strip("/").split("/")
        patient_id = None
        action = None
        if len(parts) >= 3 and parts[0] == "api":
            try:
                if parts[1] == "patients" and len(parts) >= 3 and parts[2] not in {"count"}:
                    patient_id = UUID(parts[2]); action = "patient.read"
                elif "patients" in parts:
                    index = parts.index("patients")
                    patient_id = UUID(parts[index + 1]); action = f"{parts[1]}.read"
                elif parts[1] == "documents" and len(parts) == 3 or parts[1] == "documents" and len(parts) == 4 and parts[-1] == "content":
                    async with _audit_session() as db:
                        document = await db.get(ClinicalDocument, UUID(parts[2]))
                        if document:
                            patient_id = document.patient_id
                            action = "document.download" if request.query_params.get("download") == "true" else "document.read"
                elif parts[1] == "clinical-notes" and len(parts) in {3, 4}:
                    async with _audit_session() as db:
                        note = await db.get(ClinicalNote, UUID(parts[2]))
                        if note:
                            patient_id = note.patient_id
                            action = "clinical_note.read"
                elif parts[1] == "periodontal" and len(parts) == 4 and parts[2] == "assessments":
                    async with _audit_session() as db:
                        assessment = await db.get(PeriodontalAssessment, UUID(parts[3]))
                        if assessment:
                            patient_id = assessment.patient_id
                            action = "periodontal.read"
                elif parts[1] == "treatment-plans" and len(parts) == 3:
                    async with _audit_session() as db:
                        plan = await db.get(TreatmentPlan, UUID(parts[2]))
                        if plan:
                            patient_id = plan.patient_id
                            action = "treatment_plan.read"
                elif parts[1] == "finance" and len(parts) == 5 and parts[2] == "appointments":
                    async with _audit_session() as db:
                        appointment = await db.get(Appointment, UUID(parts[3]))
                        if appointment:
                            patient_id = appointment.patient_id
                            action = "finance.read"
                elif parts[1] == "performed-procedures" and len(parts) == 3:
                    async with _audit_session() as db:
                        procedure = await db.get(PerformedProcedure, UUID(parts[2]))
                        if procedure:
                            patient_id = procedure.patient_id
                            action = "procedure.read"
            except (ValueError, IndexError):
                patient_id = None
        authorization = request.headers.get("authorization", "")
        try:
            token = authorization.split(" ", 1)[1]
            payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
            actor_id = UUID(payload["sub"])
            session_id = UUID(payload["sid"]) if payload.get("sid") else None
        except (JWTError, KeyError, ValueError, IndexError):
            actor_id = None
            session_id = None
        if parts[0] == "api" and actor_id and session_id:
            async with _audit_session() as db:
                await db.execute(update(RefreshToken).where(RefreshToken.user_id == actor_id,
                    RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None),
                    RefreshToken.last_used_at < utcnow() - timedelta(minutes=1))
                    .values(last_used_at=utcnow()))
                await db.commit()
        if len(parts) > 1 and parts[1] == "security" and parts[-1] == "export":
            action = "patient.export"
        if patient_id and actor_id and action:
            async with _audit_session() as db:
                actor = await db.get(User, actor_id)
                if actor:
                    db.add(AccessLog(clinic_id=actor.clinic_id, actor_id=actor.id, patient_id=patient_id, action=action))
                    await db.commit()
    return response
app.include_router(auth_router, prefix="/api")
app.include_router(clinic_router, prefix="/api")
app.include_router(patients_router, prefix="/api")
app.include_router(appointments_router, prefix="/api")
app.include_router(automations_router, prefix="/api")
app.include_router(appointment_procedures_router, prefix="/api")
app.include_router(procedures_router, prefix="/api")
app.include_router(performed_procedures_router, prefix="/api")
app.include_router(clinical_notes_router, prefix="/api")
app.include_router(odontogram_router, prefix="/api")
app.include_router(periodontal_router, prefix="/api")
app.include_router(treatment_plans_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(finance_router, prefix="/api")
app.include_router(equipment_router, prefix="/api")
app.include_router(waitlist_router, prefix="/api")
app.include_router(inventory_router, prefix="/api")
app.include_router(materials_router, prefix="/api")
app.include_router(forms_router, prefix="/api")
app.include_router(availability_router, prefix="/api")
app.include_router(rooms_router, prefix="/api")
app.include_router(staff_router, prefix="/api")
app.include_router(staff_management_router, prefix="/api")
app.include_router(security_router, prefix="/api")
app.include_router(reports_router, prefix="/api")
app.include_router(team_router, prefix="/api")
app.include_router(collab_router, prefix="/api")
app.include_router(cabinet_ready_router, prefix="/api")
app.include_router(comms_router, prefix="/api")
app.include_router(sterilization_router, prefix="/api")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

"""T14 routes used by patients and their representatives (``/api/portal``)."""

from datetime import timedelta
from typing import Any
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import hash_password, password_hash, verify_password
from app.database import (
    Appointment,
    Clinic,
    ClinicalDocument,
    FormTemplate,
    FormTemplateVersion,
    Patient,
    PatientForm,
    User,
    get_db,
    utcnow,
)
from app.documents import storage
from app.documents.router import SECURITY_HEADERS
from app.forms.router import _validate_answers
from app.portal.models import (
    PortalAccount,
    PortalAccountPatient,
    PortalAppointmentConfirmation,
    PortalAuditEvent,
    PortalFormRequest,
    PortalFormSubmission,
    PortalInvitation,
    PortalLoginAttempt,
    PortalPublishedDocument,
    PortalPublishedPlan,
    PortalRescheduleRequest,
    PortalSession,
)
from app.portal.security import (
    GENERIC_LOGIN_ERROR,
    SESSION_DAYS,
    PortalContext,
    aware,
    create_access_token,
    get_portal_context,
    is_locked,
    new_secret,
    token_digest,
)

router = APIRouter(prefix="/portal", tags=["portal"])
CLOSED_APPOINTMENTS = {"cancelled", "no_show", "completed"}
STATUS_LABELS = {"scheduled": "Programată", "confirmed": "Confirmată", "arrived": "Prezent", "in_progress": "În desfășurare",
                 "completed": "Încheiată", "cancelled": "Anulată", "no_show": "Neprezentare"}
NOT_FOUND = "Nu există sau nu ai acces."
INVALID_INVITATION = "Invitația nu mai este valabilă. Cere clinicii o invitație nouă."
# Verifying against a fixed hash keeps an unknown email as slow as a wrong password.
_DUMMY_HASH = password_hash.hash("portal-timing-placeholder")


class LoginIn(BaseModel):
    clinic_id: UUID
    email: EmailStr
    password: str = Field(max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class InvitationIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class AcceptIn(InvitationIn):
    password: str = Field(min_length=12, max_length=128)


class RescheduleIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    client_request_id: UUID

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Scrie un mesaj pentru clinică.")
        return value.strip()


class AnswersIn(BaseModel):
    answers: dict[str, Any]


def audit(db: AsyncSession, clinic_id: UUID, action: str, *, account_id: UUID | None = None, staff_actor_id: UUID | None = None,
          patient_id: UUID | None = None, entity_type: str | None = None, entity_id: UUID | None = None) -> None:
    db.add(PortalAuditEvent(clinic_id=clinic_id, account_id=account_id, staff_actor_id=staff_actor_id, patient_id=patient_id,
                            action=action, entity_type=entity_type, entity_id=entity_id, occurred_at=utcnow()))


def _tokens(account: PortalAccount, session: PortalSession, refresh: str) -> dict:
    return {"access_token": create_access_token(account, session), "refresh_token": refresh, "token_type": "bearer"}


# ------------------------------------------------------------------ authentication

@router.get("/clinics/{clinic_id}")
async def clinic_identity(clinic_id: UUID, db: AsyncSession = Depends(get_db)):
    clinic = await db.get(Clinic, clinic_id)
    if not clinic:
        raise HTTPException(404, NOT_FOUND)
    return {"id": str(clinic.id), "name": clinic.name}


@router.post("/auth/login")
async def login(payload: LoginIn, db: AsyncSession = Depends(get_db)):
    email = str(payload.email).lower()
    if await is_locked(db, payload.clinic_id, email):
        db.add(PortalLoginAttempt(clinic_id=payload.clinic_id, email=email, outcome="blocked", occurred_at=utcnow()))
        account_id = await db.scalar(select(PortalAccount.id).where(PortalAccount.clinic_id == payload.clinic_id, PortalAccount.email == email))
        if account_id:
            audit(db, payload.clinic_id, "login_blocked", account_id=account_id)
        await db.commit()
        raise HTTPException(401, GENERIC_LOGIN_ERROR)
    account = await db.scalar(select(PortalAccount).where(PortalAccount.clinic_id == payload.clinic_id, PortalAccount.email == email))
    valid = verify_password(payload.password, account.password_hash if account and account.password_hash else _DUMMY_HASH)
    if not account or account.status != "active" or not valid:
        db.add(PortalLoginAttempt(clinic_id=payload.clinic_id, email=email, outcome="failure", occurred_at=utcnow()))
        if account:
            audit(db, account.clinic_id, "login_failed", account_id=account.id)
        await db.commit()
        raise HTTPException(401, GENERIC_LOGIN_ERROR)
    raw, digest = new_secret()
    session = PortalSession(clinic_id=account.clinic_id, account_id=account.id, refresh_hash=digest,
                            expires_at=utcnow() + timedelta(days=SESSION_DAYS))
    db.add(session)
    db.add(PortalLoginAttempt(clinic_id=account.clinic_id, email=email, outcome="success", occurred_at=utcnow()))
    audit(db, account.clinic_id, "login", account_id=account.id)
    await db.flush()
    tokens = _tokens(account, session, raw)
    await db.commit()
    return tokens


@router.post("/auth/refresh")
async def refresh(payload: RefreshIn, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(PortalSession, PortalAccount).join(PortalAccount, PortalAccount.id == PortalSession.account_id).where(
        PortalSession.refresh_hash == token_digest(payload.refresh_token)).with_for_update())).first()
    if not row or row[0].revoked_at is not None or aware(row[0].expires_at) <= utcnow() or row[1].status != "active":
        raise HTTPException(401, "Sesiunea s-a încheiat. Autentifică-te din nou.")
    session, account = row
    raw, digest = new_secret()
    session.refresh_hash = digest
    tokens = _tokens(account, session, raw)
    await db.commit()
    return tokens


@router.post("/auth/logout", status_code=204)
async def logout(ctx: PortalContext = Depends(get_portal_context), db: AsyncSession = Depends(get_db)):
    await db.execute(update(PortalSession).where(PortalSession.id == ctx.session.id, PortalSession.revoked_at.is_(None))
                     .values(revoked_at=utcnow()))
    audit(db, ctx.account.clinic_id, "logout", account_id=ctx.account.id)
    await db.commit()


async def _usable_invitation(db: AsyncSession, raw: str):
    row = (await db.execute(select(PortalInvitation, PortalAccount, Clinic).join(
        PortalAccount, PortalAccount.id == PortalInvitation.account_id,
    ).join(Clinic, Clinic.id == PortalAccount.clinic_id).where(PortalInvitation.token_hash == token_digest(raw)))).first()
    if not row:
        return None
    invitation, account, clinic = row
    if invitation.used_at or invitation.revoked_at or aware(invitation.expires_at) <= utcnow() or account.status == "revoked":
        return None
    return invitation, account, clinic


@router.post("/auth/invitation")
async def invitation_info(payload: InvitationIn, db: AsyncSession = Depends(get_db)):
    found = await _usable_invitation(db, payload.token)
    if not found:
        raise HTTPException(404, INVALID_INVITATION)
    _invitation, account, clinic = found
    return {"clinic_id": str(clinic.id), "clinic_name": clinic.name, "full_name": account.full_name, "email": account.email}


@router.post("/auth/accept")
async def accept_invitation(payload: AcceptIn, db: AsyncSession = Depends(get_db)):
    found = await _usable_invitation(db, payload.token)
    if not found:
        raise HTTPException(404, INVALID_INVITATION)
    invitation, account, clinic = found
    now = utcnow()
    # Atomic consume: of two concurrent requests with the same token only one updates the row.
    consumed = await db.execute(update(PortalInvitation).where(
        PortalInvitation.id == invitation.id, PortalInvitation.used_at.is_(None), PortalInvitation.revoked_at.is_(None),
        PortalInvitation.expires_at > now,
    ).values(used_at=now))
    if consumed.rowcount != 1:
        await db.rollback()
        raise HTTPException(404, INVALID_INVITATION)
    account = await db.scalar(select(PortalAccount).where(PortalAccount.id == account.id).with_for_update()
                              .execution_options(populate_existing=True))
    if account.status == "revoked":
        await db.rollback()
        raise HTTPException(404, INVALID_INVITATION)
    account.password_hash = hash_password(payload.password)
    account.status = "active"
    account.activated_at = account.activated_at or now
    # A new password ends every earlier session of the account.
    await db.execute(update(PortalSession).where(PortalSession.account_id == account.id, PortalSession.revoked_at.is_(None))
                     .values(revoked_at=now))
    audit(db, account.clinic_id, "invitation_accepted", account_id=account.id, entity_type="portal_invitation", entity_id=invitation.id)
    await db.commit()
    return {"clinic_id": str(clinic.id), "clinic_name": clinic.name, "email": account.email}


# ------------------------------------------------------------------ identity and patients

async def linked_patient(db: AsyncSession, ctx: PortalContext, patient_id: UUID) -> Patient:
    patient = await db.scalar(select(Patient).join(PortalAccountPatient, PortalAccountPatient.patient_id == Patient.id).where(
        Patient.id == patient_id, Patient.clinic_id == ctx.account.clinic_id,
        PortalAccountPatient.account_id == ctx.account.id, PortalAccountPatient.clinic_id == ctx.account.clinic_id,
        PortalAccountPatient.revoked_at.is_(None),
    ))
    if not patient:
        raise HTTPException(404, NOT_FOUND)
    return patient


@router.get("/me")
async def me(ctx: PortalContext = Depends(get_portal_context), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Patient, PortalAccountPatient.relationship).join(
        PortalAccountPatient, PortalAccountPatient.patient_id == Patient.id,
    ).where(
        PortalAccountPatient.account_id == ctx.account.id, PortalAccountPatient.clinic_id == ctx.account.clinic_id,
        PortalAccountPatient.revoked_at.is_(None), Patient.clinic_id == ctx.account.clinic_id,
    ).order_by(PortalAccountPatient.created_at, Patient.id))).all()
    return {
        "account": {"full_name": ctx.account.full_name, "email": ctx.account.email},
        "clinic": {"id": str(ctx.clinic.id), "name": ctx.clinic.name, "timezone": ctx.clinic.timezone},
        "patients": [{"id": str(patient.id), "name": f"{patient.first_name} {patient.last_name}", "relationship": relationship}
                     for patient, relationship in rows],
    }


# ------------------------------------------------------------------ appointments

def _appointment_row(appointment: Appointment, doctor_name: str, zone: ZoneInfo, clinic_name: str, now,
                     confirmed_at, open_request) -> dict:
    local = aware(appointment.starts_at).astimezone(zone)
    upcoming = aware(appointment.starts_at) > now
    active = appointment.status not in CLOSED_APPOINTMENTS
    return {
        "id": str(appointment.id), "starts_at": aware(appointment.starts_at).isoformat(),
        "local_date": local.date().isoformat(), "local_time": local.strftime("%H:%M"),
        "duration_minutes": appointment.duration_minutes, "doctor_name": doctor_name, "clinic_name": clinic_name,
        "status": appointment.status, "status_label": STATUS_LABELS.get(appointment.status, appointment.status),
        "confirmed_by_patient_at": aware(confirmed_at).isoformat() if confirmed_at else None,
        "reschedule_request": {"status": open_request.status, "created_at": aware(open_request.created_at).isoformat()} if open_request else None,
        "can_confirm": upcoming and active and confirmed_at is None,
        "can_request_reschedule": upcoming and active and open_request is None,
    }


@router.get("/patients/{patient_id}/appointments")
async def appointments(patient_id: UUID, ctx: PortalContext = Depends(get_portal_context), db: AsyncSession = Depends(get_db)):
    patient = await linked_patient(db, ctx, patient_id)
    rows = (await db.execute(select(Appointment, User.full_name, PortalAppointmentConfirmation.confirmed_at).join(
        User, User.id == Appointment.doctor_id,
    ).outerjoin(PortalAppointmentConfirmation, PortalAppointmentConfirmation.appointment_id == Appointment.id).where(
        Appointment.clinic_id == ctx.account.clinic_id, Appointment.patient_id == patient.id, User.clinic_id == ctx.account.clinic_id,
    ).order_by(Appointment.starts_at.desc()).limit(200))).all()
    ids = [row[0].id for row in rows]
    requests = {row.appointment_id: row for row in (await db.scalars(select(PortalRescheduleRequest).where(
        PortalRescheduleRequest.clinic_id == ctx.account.clinic_id, PortalRescheduleRequest.appointment_id.in_(ids),
        PortalRescheduleRequest.status == "open",
    ))).all()} if ids else {}
    now, zone = utcnow(), ZoneInfo(ctx.clinic.timezone)
    upcoming, past = [], []
    for a, name, confirmed in rows:
        (upcoming if aware(a.starts_at) > now else past).append(
            _appointment_row(a, name, zone, ctx.clinic.name, now, confirmed, requests.get(a.id)))
    upcoming.reverse()
    past = past[:50]
    return {"upcoming": upcoming, "past": past}


async def _own_appointment(db: AsyncSession, ctx: PortalContext, patient: Patient, appointment_id: UUID, *, lock: bool = False) -> Appointment:
    query = select(Appointment).where(Appointment.id == appointment_id, Appointment.patient_id == patient.id,
                                      Appointment.clinic_id == ctx.account.clinic_id)
    appointment = await db.scalar(query.with_for_update() if lock else query)
    if not appointment:
        raise HTTPException(404, NOT_FOUND)
    return appointment


@router.post("/patients/{patient_id}/appointments/{appointment_id}/confirm")
async def confirm(patient_id: UUID, appointment_id: UUID, ctx: PortalContext = Depends(get_portal_context), db: AsyncSession = Depends(get_db)):
    patient = await linked_patient(db, ctx, patient_id)
    appointment = await _own_appointment(db, ctx, patient, appointment_id)
    existing = await db.scalar(select(PortalAppointmentConfirmation).where(PortalAppointmentConfirmation.appointment_id == appointment.id))
    if existing:
        return {"appointment_id": str(appointment.id), "confirmed_at": aware(existing.confirmed_at).isoformat(), "retry": True}
    if appointment.status in CLOSED_APPOINTMENTS or aware(appointment.starts_at) <= utcnow():
        raise HTTPException(409, "Programarea nu mai poate fi confirmată.")
    row = PortalAppointmentConfirmation(clinic_id=ctx.account.clinic_id, appointment_id=appointment.id, patient_id=patient.id,
                                        account_id=ctx.account.id, confirmed_at=utcnow())
    db.add(row)
    audit(db, ctx.account.clinic_id, "appointment_confirmed", account_id=ctx.account.id, patient_id=patient.id,
          entity_type="appointment", entity_id=appointment.id)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(select(PortalAppointmentConfirmation).where(PortalAppointmentConfirmation.appointment_id == appointment_id))
        if not existing:
            raise
        return {"appointment_id": str(appointment_id), "confirmed_at": aware(existing.confirmed_at).isoformat(), "retry": True}
    return {"appointment_id": str(appointment.id), "confirmed_at": aware(row.confirmed_at).isoformat(), "retry": False}


def _request_response(row: PortalRescheduleRequest, retry: bool = False) -> dict:
    return {"id": str(row.id), "appointment_id": str(row.appointment_id), "status": row.status, "message": row.message,
            "created_at": aware(row.created_at).isoformat(), "retry": retry}


@router.post("/patients/{patient_id}/appointments/{appointment_id}/reschedule-requests", status_code=201)
async def request_reschedule(patient_id: UUID, appointment_id: UUID, payload: RescheduleIn,
                             ctx: PortalContext = Depends(get_portal_context), db: AsyncSession = Depends(get_db)):
    patient = await linked_patient(db, ctx, patient_id)
    appointment = await _own_appointment(db, ctx, patient, appointment_id)

    async def replay():
        prior = await db.scalar(select(PortalRescheduleRequest).where(
            PortalRescheduleRequest.account_id == ctx.account.id, PortalRescheduleRequest.client_request_id == payload.client_request_id))
        if prior and (prior.appointment_id != appointment.id or prior.message != payload.message):
            raise HTTPException(409, "Cheia cererii a fost folosită pentru altă cerere.")
        return prior

    if prior := await replay():
        return _request_response(prior, True)
    if appointment.status in CLOSED_APPOINTMENTS or aware(appointment.starts_at) <= utcnow():
        raise HTTPException(409, "Pentru această programare nu se mai poate cere reprogramare.")
    if await db.scalar(select(PortalRescheduleRequest.id).where(
            PortalRescheduleRequest.appointment_id == appointment.id, PortalRescheduleRequest.status == "open")):
        raise HTTPException(409, "Există deja o cerere de reprogramare în așteptare.")
    row = PortalRescheduleRequest(clinic_id=ctx.account.clinic_id, appointment_id=appointment.id, patient_id=patient.id,
                                  account_id=ctx.account.id, message=payload.message, client_request_id=payload.client_request_id,
                                  status="open", created_at=utcnow())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        if prior := await replay():
            return _request_response(prior, True)
        raise HTTPException(409, "Există deja o cerere de reprogramare în așteptare.") from None
    audit(db, ctx.account.clinic_id, "reschedule_requested", account_id=ctx.account.id, patient_id=patient.id,
          entity_type="portal_reschedule_request", entity_id=row.id)
    await db.commit()
    return _request_response(row)


# ------------------------------------------------------------------ documents and plans

@router.get("/patients/{patient_id}/documents")
async def documents(patient_id: UUID, ctx: PortalContext = Depends(get_portal_context), db: AsyncSession = Depends(get_db)):
    patient = await linked_patient(db, ctx, patient_id)
    rows = (await db.execute(select(PortalPublishedDocument, ClinicalDocument).join(
        ClinicalDocument, ClinicalDocument.id == PortalPublishedDocument.document_id,
    ).where(
        PortalPublishedDocument.clinic_id == ctx.account.clinic_id, PortalPublishedDocument.patient_id == patient.id,
        PortalPublishedDocument.withdrawn_at.is_(None), ClinicalDocument.clinic_id == ctx.account.clinic_id,
        ClinicalDocument.patient_id == patient.id, ClinicalDocument.status == "active",
    ).order_by(PortalPublishedDocument.published_at.desc()))).all()
    return [{"id": str(publication.id), "filename": document.original_filename, "content_type": document.content_type,
             "size_bytes": document.size_bytes, "category": document.category,
             "published_at": aware(publication.published_at).isoformat()} for publication, document in rows]


@router.get("/patients/{patient_id}/documents/{publication_id}/content")
async def document_content(patient_id: UUID, publication_id: UUID, ctx: PortalContext = Depends(get_portal_context),
                           db: AsyncSession = Depends(get_db)):
    patient = await linked_patient(db, ctx, patient_id)
    row = (await db.execute(select(PortalPublishedDocument, ClinicalDocument).join(
        ClinicalDocument, ClinicalDocument.id == PortalPublishedDocument.document_id,
    ).where(
        PortalPublishedDocument.id == publication_id, PortalPublishedDocument.clinic_id == ctx.account.clinic_id,
        PortalPublishedDocument.patient_id == patient.id, PortalPublishedDocument.withdrawn_at.is_(None),
        ClinicalDocument.clinic_id == ctx.account.clinic_id, ClinicalDocument.patient_id == patient.id,
        ClinicalDocument.status == "active",
    ))).first()
    if not row:
        raise HTTPException(404, NOT_FOUND)
    publication, document = row
    try:
        path = storage.content_path(document.clinic_id, document.storage_key)
    except storage.StorageUnavailable as exc:
        raise HTTPException(503, "Documentele nu sunt disponibile acum.") from exc
    if not path.is_file():
        raise HTTPException(404, NOT_FOUND)
    audit(db, ctx.account.clinic_id, "document_downloaded", account_id=ctx.account.id, patient_id=patient.id,
          entity_type="portal_published_document", entity_id=publication.id)
    await db.commit()
    ascii_name = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._- ") else "_" for ch in document.original_filename)
    headers = {**SECURITY_HEADERS,
               "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(document.original_filename)}"}
    return FileResponse(path, media_type=document.content_type, headers=headers)


@router.get("/patients/{patient_id}/plans")
async def plans(patient_id: UUID, ctx: PortalContext = Depends(get_portal_context), db: AsyncSession = Depends(get_db)):
    patient = await linked_patient(db, ctx, patient_id)
    rows = (await db.scalars(select(PortalPublishedPlan).where(
        PortalPublishedPlan.clinic_id == ctx.account.clinic_id, PortalPublishedPlan.patient_id == patient.id,
        PortalPublishedPlan.withdrawn_at.is_(None),
    ).order_by(PortalPublishedPlan.published_at.desc()))).all()
    return [{"id": str(row.id), "published_at": aware(row.published_at).isoformat(), **row.snapshot} for row in rows]


# ------------------------------------------------------------------ forms

async def _portal_form(db: AsyncSession, ctx: PortalContext, patient: Patient, form_id: UUID, *, lock: bool = False):
    query = select(PatientForm, FormTemplateVersion, FormTemplate).join(
        PortalFormRequest, PortalFormRequest.patient_form_id == PatientForm.id,
    ).join(FormTemplateVersion, FormTemplateVersion.id == PatientForm.template_version_id).join(
        FormTemplate, FormTemplate.id == FormTemplateVersion.template_id,
    ).where(
        PatientForm.id == form_id, PatientForm.clinic_id == ctx.account.clinic_id, PatientForm.patient_id == patient.id,
        PortalFormRequest.clinic_id == ctx.account.clinic_id, PortalFormRequest.patient_id == patient.id,
        FormTemplateVersion.clinic_id == ctx.account.clinic_id,
    )
    row = (await db.execute(query.with_for_update(of=PatientForm) if lock else query)).first()
    if not row:
        raise HTTPException(404, NOT_FOUND)
    return row


FORM_LABELS = {"draft": "De completat", "submitted": "Trimis", "validated": "Verificat de clinică",
               "needs_clarification": "Clinica a cerut clarificări"}


def _form_view(form: PatientForm, version: FormTemplateVersion, template: FormTemplate, *, full: bool) -> dict:
    view = {"id": str(form.id), "name": template.name, "status": form.status, "status_label": FORM_LABELS[form.status],
            "editable": form.status in {"draft", "needs_clarification"},
            "submitted_at": aware(form.submitted_at).isoformat() if form.submitted_at else None}
    if full:
        view["definition"] = version.definition
        view["answers"] = form.answers
    return view


@router.get("/patients/{patient_id}/forms")
async def forms(patient_id: UUID, ctx: PortalContext = Depends(get_portal_context), db: AsyncSession = Depends(get_db)):
    patient = await linked_patient(db, ctx, patient_id)
    rows = (await db.execute(select(PatientForm, FormTemplateVersion, FormTemplate).join(
        PortalFormRequest, PortalFormRequest.patient_form_id == PatientForm.id,
    ).join(FormTemplateVersion, FormTemplateVersion.id == PatientForm.template_version_id).join(
        FormTemplate, FormTemplate.id == FormTemplateVersion.template_id,
    ).where(
        PatientForm.clinic_id == ctx.account.clinic_id, PatientForm.patient_id == patient.id,
        PortalFormRequest.clinic_id == ctx.account.clinic_id, PortalFormRequest.patient_id == patient.id,
    ).order_by(PortalFormRequest.sent_at.desc()))).all()
    return [_form_view(form, version, template, full=False) for form, version, template in rows]


@router.get("/patients/{patient_id}/forms/{form_id}")
async def form_detail(patient_id: UUID, form_id: UUID, ctx: PortalContext = Depends(get_portal_context), db: AsyncSession = Depends(get_db)):
    patient = await linked_patient(db, ctx, patient_id)
    form, version, template = await _portal_form(db, ctx, patient, form_id)
    return _form_view(form, version, template, full=True)


@router.put("/patients/{patient_id}/forms/{form_id}/answers")
async def save_answers(patient_id: UUID, form_id: UUID, payload: AnswersIn, ctx: PortalContext = Depends(get_portal_context),
                       db: AsyncSession = Depends(get_db)):
    patient = await linked_patient(db, ctx, patient_id)
    form, version, template = await _portal_form(db, ctx, patient, form_id, lock=True)
    if form.status not in {"draft", "needs_clarification"}:
        raise HTTPException(409, "Formularul a fost deja trimis.")
    _validate_answers(version.definition, payload.answers, submitting=False)
    form.answers = payload.answers
    await db.commit()
    return _form_view(form, version, template, full=True)


@router.post("/patients/{patient_id}/forms/{form_id}/submit")
async def submit_form(patient_id: UUID, form_id: UUID, payload: AnswersIn, ctx: PortalContext = Depends(get_portal_context),
                      db: AsyncSession = Depends(get_db)):
    patient = await linked_patient(db, ctx, patient_id)
    form, version, template = await _portal_form(db, ctx, patient, form_id, lock=True)
    if form.status not in {"draft", "needs_clarification"}:
        raise HTTPException(409, "Formularul a fost deja trimis.")
    _validate_answers(version.definition, payload.answers, submitting=True)
    number = (await db.scalar(select(func.max(PortalFormSubmission.revision_number)).where(
        PortalFormSubmission.patient_form_id == form.id)) or 0) + 1
    now = utcnow()
    db.add(PortalFormSubmission(clinic_id=ctx.account.clinic_id, patient_form_id=form.id, account_id=ctx.account.id,
                                revision_number=number, answers=payload.answers, submitted_at=now))
    form.answers, form.status, form.submitted_at, form.clarification_reason = payload.answers, "submitted", now, None
    audit(db, ctx.account.clinic_id, "form_submitted", account_id=ctx.account.id, patient_id=patient.id,
          entity_type="patient_form", entity_id=form.id)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Formularul a fost trimis simultan. Reîncarcă pagina.") from None
    return {**_form_view(form, version, template, full=True), "revision_number": number}

"""T14 staff side of the patient portal (``/api/portal-staff``).

Roles:
- access (invite, revoke, representatives): clinic_manager, administrator, doctor, reception;
- publish documents and plans: clinic_manager, administrator, doctor (the T08/T11 roles);
- send forms and read the section: every staff role (T12 lets every role create a patient form);
- reschedule requests: reception, clinic_manager, administrator;
- portal audit: clinic_manager, administrator.
Another clinic's patient, account, publication or request is 404.
"""

from collections import defaultdict
from datetime import timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import (
    Appointment,
    Clinic,
    ClinicalDocument,
    FormTemplate,
    FormTemplateVersion,
    Patient,
    PatientForm,
    TreatmentPlan,
    User,
    get_db,
    utcnow,
)
from app.portal.models import (
    PortalAccount,
    PortalAccountPatient,
    PortalAppointmentConfirmation,
    PortalAuditEvent,
    PortalFormRequest,
    PortalFormSubmission,
    PortalInvitation,
    PortalPublishedDocument,
    PortalPublishedPlan,
    PortalRescheduleRequest,
    PortalSession,
)
from app.portal.patient_router import audit
from app.portal.security import INVITATION_HOURS, aware, new_secret
from app.treatment_plans.router import _item_progress, _load_content, _money

router = APIRouter(prefix="/portal-staff", tags=["portal-staff"])
STAFF = ("clinic_manager", "doctor", "assistant", "reception", "administrator")
ACCESS = ("clinic_manager", "administrator", "doctor", "reception")
CLINICAL = ("clinic_manager", "administrator", "doctor")
REQUESTS = ("reception", "clinic_manager", "administrator")
MANAGERS = ("clinic_manager", "administrator")
RELATIONSHIPS = {"self": "Pacientul", "parent": "Părinte", "guardian": "Tutore", "other": "Reprezentant"}
ACCOUNT_LABELS = {"invited": "Invitat, fără parolă", "active": "Activ", "revoked": "Acces revocat"}
PUBLISHABLE_PLAN_STATUSES = ("draft", "accepted", "in_progress", "completed")


class AccessIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    relationship: Literal["self", "parent", "guardian", "other"]

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        if len(value.strip()) < 2:
            raise ValueError("Numele este obligatoriu.")
        return value.strip()


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Motivul este obligatoriu.")
        return value.strip()


class DocumentIn(BaseModel):
    document_id: UUID


class PlanIn(BaseModel):
    plan_id: UUID


class FormIn(BaseModel):
    template_version_id: UUID
    client_request_id: UUID


class ResolveIn(BaseModel):
    resolution: Literal["rescheduled", "rejected"]
    note: str | None = Field(default=None, max_length=500)


async def _patient(db: AsyncSession, user: User, patient_id: UUID) -> Patient:
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Pacient inexistent.")
    return patient


def _iso(value) -> str | None:
    return aware(value).isoformat() if value else None


def _invitation_url(raw: str) -> str:
    return f"/portal/invitatie?token={raw}"


async def _new_invitation(db: AsyncSession, user: User, account: PortalAccount) -> tuple[PortalInvitation, str]:
    now = utcnow()
    await db.execute(update(PortalInvitation).where(
        PortalInvitation.account_id == account.id, PortalInvitation.used_at.is_(None), PortalInvitation.revoked_at.is_(None),
    ).values(revoked_at=now))
    raw, digest = new_secret()
    invitation = PortalInvitation(clinic_id=account.clinic_id, account_id=account.id, token_hash=digest,
                                  expires_at=now + timedelta(hours=INVITATION_HOURS), created_by_id=user.id, created_at=now)
    db.add(invitation)
    await db.flush()
    return invitation, raw


# ------------------------------------------------------------------ patient section

@router.get("/patients/{patient_id}")
async def patient_section(patient_id: UUID, user: User = Depends(require_roles(*STAFF)), db: AsyncSession = Depends(get_db)):
    patient = await _patient(db, user, patient_id)
    links = (await db.execute(select(PortalAccountPatient, PortalAccount).join(
        PortalAccount, PortalAccount.id == PortalAccountPatient.account_id,
    ).where(
        PortalAccountPatient.clinic_id == user.clinic_id, PortalAccountPatient.patient_id == patient.id,
        PortalAccount.clinic_id == user.clinic_id,
    ).order_by(PortalAccountPatient.created_at, PortalAccountPatient.id))).all()
    account_ids = [account.id for _link, account in links]
    pending = {}
    if account_ids:
        for invitation in (await db.scalars(select(PortalInvitation).where(
                PortalInvitation.account_id.in_(account_ids), PortalInvitation.used_at.is_(None),
                PortalInvitation.revoked_at.is_(None), PortalInvitation.expires_at > utcnow()))).all():
            pending[invitation.account_id] = invitation
    clinical = user.role in CLINICAL
    documents = plans = None
    if clinical:
        documents = [{"id": str(publication.id), "document_id": str(document.id), "filename": document.original_filename,
                      "published_at": _iso(publication.published_at), "withdrawn_at": _iso(publication.withdrawn_at),
                      "document_archived": document.status != "active"}
                     for publication, document in (await db.execute(select(PortalPublishedDocument, ClinicalDocument).join(
                         ClinicalDocument, ClinicalDocument.id == PortalPublishedDocument.document_id,
                     ).where(PortalPublishedDocument.clinic_id == user.clinic_id, PortalPublishedDocument.patient_id == patient.id)
                       .order_by(PortalPublishedDocument.published_at.desc()))).all()]
        plans = [{"id": str(row.id), "plan_id": str(row.plan_id), "title": row.snapshot.get("title"), "version": row.plan_version,
                  "published_at": _iso(row.published_at), "withdrawn_at": _iso(row.withdrawn_at)}
                 for row in (await db.scalars(select(PortalPublishedPlan).where(
                     PortalPublishedPlan.clinic_id == user.clinic_id, PortalPublishedPlan.patient_id == patient.id,
                 ).order_by(PortalPublishedPlan.published_at.desc()))).all()]
    form_rows = (await db.execute(select(PortalFormRequest, PatientForm, FormTemplate.name).join(
        PatientForm, PatientForm.id == PortalFormRequest.patient_form_id,
    ).join(FormTemplateVersion, FormTemplateVersion.id == PatientForm.template_version_id).join(
        FormTemplate, FormTemplate.id == FormTemplateVersion.template_id,
    ).where(PortalFormRequest.clinic_id == user.clinic_id, PortalFormRequest.patient_id == patient.id,
            PatientForm.clinic_id == user.clinic_id).order_by(PortalFormRequest.sent_at.desc()))).all()
    form_ids = [form.id for _request, form, _name in form_rows]
    submissions = defaultdict(list)
    if form_ids:
        for submission, name in (await db.execute(select(PortalFormSubmission, PortalAccount.full_name).join(
                PortalAccount, PortalAccount.id == PortalFormSubmission.account_id,
        ).where(PortalFormSubmission.clinic_id == user.clinic_id, PortalFormSubmission.patient_form_id.in_(form_ids))
                .order_by(PortalFormSubmission.revision_number))).all():
            submissions[submission.patient_form_id].append({
                "revision_number": submission.revision_number, "submitted_at": _iso(submission.submitted_at),
                "completed_by": name, "label": "Completat de pacient",
                **({"answers": submission.answers} if clinical else {}),
            })
    return {
        "patient_id": str(patient.id),
        "login_path": f"/portal?clinic={user.clinic_id}",
        "accounts": [{
            "link_id": str(link.id), "account_id": str(account.id), "full_name": account.full_name, "email": account.email,
            "relationship": link.relationship, "relationship_label": RELATIONSHIPS.get(link.relationship, link.relationship),
            "link_active": link.revoked_at is None, "status": account.status, "status_label": ACCOUNT_LABELS[account.status],
            "activated_at": _iso(account.activated_at),
            "invitation_expires_at": _iso(pending[account.id].expires_at) if account.id in pending else None,
        } for link, account in links],
        "documents": documents, "plans": plans,
        "forms": [{"id": str(form.id), "name": name, "status": form.status, "source": form.source, "sent_at": _iso(request.sent_at),
                   "submissions": submissions[form.id]} for request, form, name in form_rows],
        "can": {"manage_access": user.role in ACCESS, "publish": clinical, "send_forms": True},
    }


# ------------------------------------------------------------------ accounts, invitations, representatives

@router.post("/patients/{patient_id}/access", status_code=201)
async def grant_access(patient_id: UUID, payload: AccessIn, user: User = Depends(require_roles(*ACCESS)), db: AsyncSession = Depends(get_db)):
    patient = await _patient(db, user, patient_id)
    email = str(payload.email).lower()
    account = await db.scalar(select(PortalAccount).where(PortalAccount.clinic_id == user.clinic_id, PortalAccount.email == email)
                              .with_for_update())
    if account is None:
        account = PortalAccount(clinic_id=user.clinic_id, email=email, full_name=payload.full_name, status="invited",
                                created_by_id=user.id, created_at=utcnow())
        db.add(account)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "Contul a fost creat simultan. Reîncarcă și încearcă din nou.") from None
        audit(db, user.clinic_id, "account_created", staff_actor_id=user.id, patient_id=patient.id, entity_type="portal_account", entity_id=account.id)
    elif account.status == "revoked":
        account.status, account.revoked_at, account.revoked_by_id, account.revoke_reason = "invited", None, None, None
        audit(db, user.clinic_id, "account_reinvited", staff_actor_id=user.id, patient_id=patient.id, entity_type="portal_account", entity_id=account.id)
    link = await db.scalar(select(PortalAccountPatient).where(PortalAccountPatient.account_id == account.id,
                                                              PortalAccountPatient.patient_id == patient.id))
    if link and link.revoked_at is None and account.status == "active":
        raise HTTPException(409, "Contul are deja acces la acest pacient.")
    if link:
        link.revoked_at, link.revoked_by_id, link.relationship = None, None, payload.relationship
    else:
        link = PortalAccountPatient(clinic_id=user.clinic_id, account_id=account.id, patient_id=patient.id,
                                    relationship=payload.relationship, created_by_id=user.id, created_at=utcnow())
        db.add(link)
    await db.flush()
    audit(db, user.clinic_id, "access_granted", staff_actor_id=user.id, patient_id=patient.id, entity_type="portal_account_patient", entity_id=link.id)
    url = expires = None
    if account.status != "active":
        invitation, raw = await _new_invitation(db, user, account)
        url, expires = _invitation_url(raw), _iso(invitation.expires_at)
        audit(db, user.clinic_id, "invitation_created", staff_actor_id=user.id, patient_id=patient.id, entity_type="portal_invitation", entity_id=invitation.id)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Accesul a fost acordat simultan. Reîncarcă pagina.") from None
    return {"account_id": str(account.id), "link_id": str(link.id), "status": account.status,
            "invitation_url": url, "invitation_expires_at": expires}


async def _account(db: AsyncSession, user: User, account_id: UUID, *, lock: bool = False) -> PortalAccount:
    query = select(PortalAccount).where(PortalAccount.id == account_id, PortalAccount.clinic_id == user.clinic_id)
    account = await db.scalar(query.with_for_update() if lock else query)
    if not account:
        raise HTTPException(404, "Cont inexistent.")
    return account


@router.post("/accounts/{account_id}/invitation", status_code=201)
async def reissue_invitation(account_id: UUID, user: User = Depends(require_roles(*ACCESS)), db: AsyncSession = Depends(get_db)):
    account = await _account(db, user, account_id, lock=True)
    if account.status != "invited":
        raise HTTPException(409, "Invitația se poate retrimite numai unui cont fără parolă.")
    invitation, raw = await _new_invitation(db, user, account)
    audit(db, user.clinic_id, "invitation_created", staff_actor_id=user.id, entity_type="portal_invitation", entity_id=invitation.id)
    await db.commit()
    return {"account_id": str(account.id), "invitation_url": _invitation_url(raw), "invitation_expires_at": _iso(invitation.expires_at)}


@router.post("/accounts/{account_id}/revoke")
async def revoke_account(account_id: UUID, payload: ReasonIn, user: User = Depends(require_roles(*ACCESS)), db: AsyncSession = Depends(get_db)):
    account = await _account(db, user, account_id, lock=True)
    if account.status == "revoked":
        raise HTTPException(409, "Accesul este deja revocat.")
    now = utcnow()
    account.status, account.revoked_at, account.revoked_by_id, account.revoke_reason = "revoked", now, user.id, payload.reason
    await db.execute(update(PortalSession).where(PortalSession.account_id == account.id, PortalSession.revoked_at.is_(None)).values(revoked_at=now))
    await db.execute(update(PortalInvitation).where(PortalInvitation.account_id == account.id, PortalInvitation.used_at.is_(None),
                                                    PortalInvitation.revoked_at.is_(None)).values(revoked_at=now))
    audit(db, user.clinic_id, "account_revoked", staff_actor_id=user.id, entity_type="portal_account", entity_id=account.id)
    await db.commit()
    return {"account_id": str(account.id), "status": account.status}


@router.post("/links/{link_id}/revoke")
async def revoke_link(link_id: UUID, user: User = Depends(require_roles(*ACCESS)), db: AsyncSession = Depends(get_db)):
    link = await db.scalar(select(PortalAccountPatient).where(PortalAccountPatient.id == link_id, PortalAccountPatient.clinic_id == user.clinic_id)
                           .with_for_update())
    if not link:
        raise HTTPException(404, "Legătură inexistentă.")
    if link.revoked_at is not None:
        raise HTTPException(409, "Legătura este deja revocată.")
    link.revoked_at, link.revoked_by_id = utcnow(), user.id
    audit(db, user.clinic_id, "access_revoked", staff_actor_id=user.id, patient_id=link.patient_id, entity_type="portal_account_patient", entity_id=link.id)
    await db.commit()
    return {"link_id": str(link.id), "revoked": True}


# ------------------------------------------------------------------ publications

@router.post("/patients/{patient_id}/documents", status_code=201)
async def publish_document(patient_id: UUID, payload: DocumentIn, user: User = Depends(require_roles(*CLINICAL)), db: AsyncSession = Depends(get_db)):
    patient = await _patient(db, user, patient_id)
    document = await db.scalar(select(ClinicalDocument).where(
        ClinicalDocument.id == payload.document_id, ClinicalDocument.clinic_id == user.clinic_id, ClinicalDocument.patient_id == patient.id))
    if not document:
        raise HTTPException(404, "Document inexistent.")
    if document.status != "active":
        raise HTTPException(409, "Un document arhivat nu se poate publica.")
    existing = await db.scalar(select(PortalPublishedDocument).where(PortalPublishedDocument.document_id == document.id,
                                                                     PortalPublishedDocument.withdrawn_at.is_(None)))
    if existing:
        return {"id": str(existing.id), "document_id": str(document.id), "published_at": _iso(existing.published_at), "retry": True}
    row = PortalPublishedDocument(clinic_id=user.clinic_id, patient_id=patient.id, document_id=document.id, published_by_id=user.id, published_at=utcnow())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Documentul a fost publicat simultan. Reîncarcă pagina.") from None
    audit(db, user.clinic_id, "document_published", staff_actor_id=user.id, patient_id=patient.id, entity_type="portal_published_document", entity_id=row.id)
    await db.commit()
    return {"id": str(row.id), "document_id": str(document.id), "published_at": _iso(row.published_at), "retry": False}


@router.post("/document-publications/{publication_id}/withdraw")
async def withdraw_document(publication_id: UUID, user: User = Depends(require_roles(*CLINICAL)), db: AsyncSession = Depends(get_db)):
    row = await db.scalar(select(PortalPublishedDocument).where(PortalPublishedDocument.id == publication_id,
                                                                PortalPublishedDocument.clinic_id == user.clinic_id).with_for_update())
    if not row:
        raise HTTPException(404, "Publicare inexistentă.")
    if row.withdrawn_at is not None:
        raise HTTPException(409, "Publicarea este deja retrasă.")
    row.withdrawn_at, row.withdrawn_by_id = utcnow(), user.id
    audit(db, user.clinic_id, "document_withdrawn", staff_actor_id=user.id, patient_id=row.patient_id, entity_type="portal_published_document", entity_id=row.id)
    await db.commit()
    return {"id": str(row.id), "withdrawn_at": _iso(row.withdrawn_at)}


def portal_plan_snapshot(plan: TreatmentPlan, stages, items, performed) -> dict:
    """Whitelist copy for the patient: stages, procedures and estimates. No notes, no internal ids."""
    by_stage = defaultdict(list)
    totals: dict[str, dict] = {}
    for item in items:
        if _item_progress(item, performed.get(item.performed_procedure_id)) == "cancelled":
            continue
        by_stage[item.stage_id].append(item)
        total = totals.setdefault(item.currency, {"currency": item.currency, "amount": 0, "minutes": 0})
        total["amount"] += item.estimated_price
        total["minutes"] += item.estimated_duration_minutes
    return {
        "title": plan.title, "version": plan.version,
        "stages": [{"position": index, "title": stage.title, "items": [{
            "procedure_name": item.procedure_name, "tooth_fdi": item.tooth_fdi, "tooth_surface": item.tooth_surface,
            "estimated_price": _money(item.estimated_price), "currency": item.currency,
            "estimated_duration_minutes": item.estimated_duration_minutes,
        } for item in by_stage[stage.id]]} for index, stage in enumerate(stages, start=1) if by_stage[stage.id]],
        "totals": [{"currency": total["currency"], "estimated_total": _money(total["amount"]), "estimated_minutes": total["minutes"]}
                   for _currency, total in sorted(totals.items())],
    }


@router.post("/patients/{patient_id}/plans", status_code=201)
async def publish_plan(patient_id: UUID, payload: PlanIn, user: User = Depends(require_roles(*CLINICAL)), db: AsyncSession = Depends(get_db)):
    patient = await _patient(db, user, patient_id)
    plan = await db.scalar(select(TreatmentPlan).where(TreatmentPlan.id == payload.plan_id, TreatmentPlan.clinic_id == user.clinic_id,
                                                       TreatmentPlan.patient_id == patient.id).with_for_update())
    if not plan:
        raise HTTPException(404, "Plan inexistent.")
    if plan.status not in PUBLISHABLE_PLAN_STATUSES:
        raise HTTPException(409, "Un plan anulat nu se poate publica.")
    stages, items, performed, _appointments = await _load_content(db, plan)
    snapshot = portal_plan_snapshot(plan, stages, items, performed)
    existing = await db.scalar(select(PortalPublishedPlan).where(PortalPublishedPlan.plan_id == plan.id, PortalPublishedPlan.withdrawn_at.is_(None)))
    # A draft edit keeps the plan version, so "already published" means the same frozen content.
    if existing and existing.snapshot == snapshot:
        return {"id": str(existing.id), "plan_id": str(plan.id), "version": existing.plan_version, "retry": True}
    now = utcnow()
    if existing:
        existing.withdrawn_at, existing.withdrawn_by_id = now, user.id
        audit(db, user.clinic_id, "plan_withdrawn", staff_actor_id=user.id, patient_id=patient.id, entity_type="portal_published_plan", entity_id=existing.id)
        await db.flush()
    row = PortalPublishedPlan(clinic_id=user.clinic_id, patient_id=patient.id, plan_id=plan.id, plan_version=plan.version,
                              snapshot=snapshot, published_by_id=user.id, published_at=now)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Planul a fost publicat simultan. Reîncarcă pagina.") from None
    audit(db, user.clinic_id, "plan_published", staff_actor_id=user.id, patient_id=patient.id, entity_type="portal_published_plan", entity_id=row.id)
    await db.commit()
    return {"id": str(row.id), "plan_id": str(plan.id), "version": row.plan_version, "retry": False}


@router.post("/plan-publications/{publication_id}/withdraw")
async def withdraw_plan(publication_id: UUID, user: User = Depends(require_roles(*CLINICAL)), db: AsyncSession = Depends(get_db)):
    row = await db.scalar(select(PortalPublishedPlan).where(PortalPublishedPlan.id == publication_id,
                                                            PortalPublishedPlan.clinic_id == user.clinic_id).with_for_update())
    if not row:
        raise HTTPException(404, "Publicare inexistentă.")
    if row.withdrawn_at is not None:
        raise HTTPException(409, "Publicarea este deja retrasă.")
    row.withdrawn_at, row.withdrawn_by_id = utcnow(), user.id
    audit(db, user.clinic_id, "plan_withdrawn", staff_actor_id=user.id, patient_id=row.patient_id, entity_type="portal_published_plan", entity_id=row.id)
    await db.commit()
    return {"id": str(row.id), "withdrawn_at": _iso(row.withdrawn_at)}


# ------------------------------------------------------------------ forms

@router.post("/patients/{patient_id}/forms", status_code=201)
async def send_form(patient_id: UUID, payload: FormIn, user: User = Depends(require_roles(*STAFF)), db: AsyncSession = Depends(get_db)):
    patient = await _patient(db, user, patient_id)

    async def replay():
        prior = await db.scalar(select(PortalFormRequest).where(PortalFormRequest.sent_by_id == user.id,
                                                                PortalFormRequest.client_request_id == payload.client_request_id))
        if prior and prior.patient_id != patient.id:
            raise HTTPException(409, "Cheia cererii a fost folosită pentru alt pacient.")
        return prior

    if prior := await replay():
        return {"id": str(prior.patient_form_id), "request_id": str(prior.id), "retry": True}
    version = await db.scalar(select(FormTemplateVersion).where(FormTemplateVersion.id == payload.template_version_id,
                                                                FormTemplateVersion.clinic_id == user.clinic_id))
    if not version or version.status != "published":
        raise HTTPException(404, "Formular publicat inexistent.")
    now = utcnow()
    form = PatientForm(clinic_id=user.clinic_id, patient_id=patient.id, template_version_id=version.id, source="patient",
                       status="draft", answers={}, created_by_id=user.id, created_at=now)
    db.add(form)
    await db.flush()
    request = PortalFormRequest(clinic_id=user.clinic_id, patient_id=patient.id, patient_form_id=form.id, sent_by_id=user.id,
                                sent_at=now, client_request_id=payload.client_request_id)
    db.add(request)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        if prior := await replay():
            return {"id": str(prior.patient_form_id), "request_id": str(prior.id), "retry": True}
        raise
    audit(db, user.clinic_id, "form_sent", staff_actor_id=user.id, patient_id=patient.id, entity_type="patient_form", entity_id=form.id)
    await db.commit()
    return {"id": str(form.id), "request_id": str(request.id), "retry": False}


# ------------------------------------------------------------------ reschedule requests and confirmations

@router.get("/reschedule-requests")
async def reschedule_requests(status: Literal["open", "resolved", "all"] = "open", user: User = Depends(require_roles(*REQUESTS)),
                              db: AsyncSession = Depends(get_db)):
    clinic = await db.get(Clinic, user.clinic_id)
    zone = ZoneInfo(clinic.timezone)
    query = select(PortalRescheduleRequest, Appointment, Patient, User.full_name, PortalAccount.full_name).join(
        Appointment, Appointment.id == PortalRescheduleRequest.appointment_id,
    ).join(Patient, Patient.id == PortalRescheduleRequest.patient_id).join(User, User.id == Appointment.doctor_id).join(
        PortalAccount, PortalAccount.id == PortalRescheduleRequest.account_id,
    ).where(PortalRescheduleRequest.clinic_id == user.clinic_id, Appointment.clinic_id == user.clinic_id, Patient.clinic_id == user.clinic_id)
    if status == "open":
        query = query.where(PortalRescheduleRequest.status == "open")
    elif status == "resolved":
        query = query.where(PortalRescheduleRequest.status != "open")
    rows = (await db.execute(query.order_by(PortalRescheduleRequest.created_at, PortalRescheduleRequest.id).limit(500))).all()
    result = []
    for request, appointment, patient, doctor_name, account_name in rows:
        local = aware(appointment.starts_at).astimezone(zone)
        result.append({
            "id": str(request.id), "status": request.status, "message": request.message, "created_at": _iso(request.created_at),
            "patient_id": str(patient.id), "patient_name": f"{patient.first_name} {patient.last_name}", "requested_by": account_name,
            "appointment_id": str(appointment.id), "appointment_starts_at": _iso(appointment.starts_at),
            "appointment_local": f"{local.date().isoformat()} {local.strftime('%H:%M')}", "appointment_status": appointment.status,
            "doctor_name": doctor_name, "resolved_at": _iso(request.resolved_at), "resolution_note": request.resolution_note,
        })
    return result


@router.post("/reschedule-requests/{request_id}/resolve")
async def resolve_request(request_id: UUID, payload: ResolveIn, user: User = Depends(require_roles(*REQUESTS)), db: AsyncSession = Depends(get_db)):
    row = await db.scalar(select(PortalRescheduleRequest).where(PortalRescheduleRequest.id == request_id,
                                                                PortalRescheduleRequest.clinic_id == user.clinic_id).with_for_update())
    if not row:
        raise HTTPException(404, "Cerere inexistentă.")
    if row.status != "open":
        raise HTTPException(409, "Cererea este deja rezolvată.")
    row.status, row.resolved_by_id, row.resolved_at = payload.resolution, user.id, utcnow()
    row.resolution_note = payload.note.strip() if payload.note and payload.note.strip() else None
    audit(db, user.clinic_id, f"reschedule_{payload.resolution}", staff_actor_id=user.id, patient_id=row.patient_id,
          entity_type="portal_reschedule_request", entity_id=row.id)
    await db.commit()
    return {"id": str(row.id), "status": row.status, "resolved_at": _iso(row.resolved_at)}


@router.get("/patients/{patient_id}/confirmations")
async def confirmations(patient_id: UUID, user: User = Depends(require_roles(*STAFF)), db: AsyncSession = Depends(get_db)):
    patient = await _patient(db, user, patient_id)
    rows = (await db.execute(select(PortalAppointmentConfirmation, PortalAccount.full_name).join(
        PortalAccount, PortalAccount.id == PortalAppointmentConfirmation.account_id,
    ).where(PortalAppointmentConfirmation.clinic_id == user.clinic_id, PortalAppointmentConfirmation.patient_id == patient.id)
      .order_by(PortalAppointmentConfirmation.confirmed_at.desc()))).all()
    return [{"appointment_id": str(row.appointment_id), "confirmed_at": _iso(row.confirmed_at), "confirmed_by": name} for row, name in rows]


@router.get("/patients/{patient_id}/audit")
async def portal_audit(patient_id: UUID, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)):
    patient = await _patient(db, user, patient_id)
    account_ids = select(PortalAccountPatient.account_id).where(PortalAccountPatient.patient_id == patient.id,
                                                                PortalAccountPatient.clinic_id == user.clinic_id)
    rows = (await db.execute(select(PortalAuditEvent, PortalAccount.full_name, User.full_name).outerjoin(
        PortalAccount, PortalAccount.id == PortalAuditEvent.account_id,
    ).outerjoin(User, User.id == PortalAuditEvent.staff_actor_id).where(
        PortalAuditEvent.clinic_id == user.clinic_id,
        (PortalAuditEvent.patient_id == patient.id) | (PortalAuditEvent.patient_id.is_(None) & PortalAuditEvent.account_id.in_(account_ids)),
    ).order_by(PortalAuditEvent.occurred_at.desc()).limit(500))).all()
    return [{"action": event.action, "occurred_at": _iso(event.occurred_at), "account": account_name, "staff": staff_name,
             "patient_id": str(event.patient_id) if event.patient_id else None, "entity_type": event.entity_type,
             "entity_id": str(event.entity_id) if event.entity_id else None} for event, account_name, staff_name in rows]

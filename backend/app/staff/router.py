import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_current_user, hash_password, require_roles
from app.database import Appointment, AuditEvent, StaffInvitation, User, get_db, utcnow

router = APIRouter(prefix="/staff", tags=["staff"])
MANAGEMENT_ROLES = ("clinic_manager", "administrator")
INVITABLE_ROLES = ("doctor", "assistant", "reception")
ACTIVE_APPOINTMENT_STATUSES = ("scheduled", "confirmed", "arrived", "in_progress", "completed")
INVITATION_DAYS = 3


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class StaffResponse(BaseModel):
    id: UUID
    clinic_id: UUID
    full_name: str
    email: EmailStr
    role: str
    specialization: str | None
    phone: str | None
    is_active: bool
    deactivated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class StaffDetailResponse(StaffResponse):
    upcoming_appointment_count: int


class InvitationCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    role: str
    expires_in_days: int = Field(default=INVITATION_DAYS, ge=1, le=7)

    def normalized(self) -> tuple[str, str, str]:
        return normalize_email(self.email), self.full_name.strip(), self.role


class InvitationResponse(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str
    role: str
    expires_at: datetime
    revoked_at: datetime | None
    accepted_at: datetime | None
    created_at: datetime
    invitation_url: str | None = None
    model_config = ConfigDict(from_attributes=True)


class InvitationAccept(BaseModel):
    password: str = Field(min_length=12, max_length=128)


class StaffStatusResponse(BaseModel):
    staff: StaffResponse
    affected_appointments: list[dict[str, object]] = []


async def require_invitation_manager(user: User = Depends(require_roles(*MANAGEMENT_ROLES))) -> User:
    return user


def invitation_response(invitation: StaffInvitation, raw_token: str | None = None) -> InvitationResponse:
    return InvitationResponse.model_validate({**invitation.__dict__, "invitation_url": f"/staff/invitations/accept?token={raw_token}" if raw_token else None}, from_attributes=True)


@router.get("", response_model=list[StaffResponse])
async def list_staff(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[StaffResponse]:
    if user.role not in (*MANAGEMENT_ROLES, *INVITABLE_ROLES):
        raise HTTPException(403, "Staff directory is unavailable for this role")
    return list((await db.scalars(select(User).where(User.clinic_id == user.clinic_id).order_by(User.full_name))).all())


@router.get("/members/{staff_id}", response_model=StaffDetailResponse)
async def get_staff(staff_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> StaffDetailResponse:
    if user.role not in (*MANAGEMENT_ROLES, *INVITABLE_ROLES):
        raise HTTPException(403, "Staff directory is unavailable for this role")
    staff = await db.scalar(select(User).where(User.id == staff_id, User.clinic_id == user.clinic_id))
    if not staff:
        raise HTTPException(404, "Staff member not found")
    upcoming = int(await db.scalar(select(func.count(Appointment.id)).where(Appointment.clinic_id == user.clinic_id, Appointment.starts_at > utcnow(), Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES), (Appointment.doctor_id == staff.id) | (Appointment.assistant_id == staff.id))) or 0)
    return StaffDetailResponse.model_validate({**staff.__dict__, "upcoming_appointment_count": upcoming}, from_attributes=True)


@router.post("/invitations", response_model=InvitationResponse, status_code=201)
async def create_invitation(payload: InvitationCreate, user: User = Depends(require_invitation_manager), db: AsyncSession = Depends(get_db)) -> InvitationResponse:
    email, full_name, role = payload.normalized()
    if role not in INVITABLE_ROLES:
        raise HTTPException(422, "Only doctor, assistant, or reception invitations are allowed")
    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(409, "An account already exists for this email")
    pending = await db.scalars(select(StaffInvitation).where(StaffInvitation.clinic_id == user.clinic_id, StaffInvitation.email == email, StaffInvitation.accepted_at.is_(None), StaffInvitation.revoked_at.is_(None)))
    for invitation in pending:
        invitation.revoked_at = utcnow()
    raw = secrets.token_urlsafe(48)
    invitation = StaffInvitation(clinic_id=user.clinic_id, invited_by_id=user.id, email=email, full_name=full_name, role=role, token_hash=token_hash(raw), expires_at=utcnow() + timedelta(days=payload.expires_in_days))
    db.add(invitation)
    await db.flush()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="staff_invitation", entity_id=invitation.id, action="created"))
    await db.commit()
    await db.refresh(invitation)
    return invitation_response(invitation, raw)


@router.get("/invitations", response_model=list[InvitationResponse])
async def list_invitations(user: User = Depends(require_invitation_manager), db: AsyncSession = Depends(get_db)) -> list[InvitationResponse]:
    invitations = (await db.scalars(select(StaffInvitation).where(StaffInvitation.clinic_id == user.clinic_id).order_by(StaffInvitation.created_at.desc()))).all()
    return [invitation_response(item) for item in invitations]


@router.post("/invitations/{invitation_id}/revoke", response_model=InvitationResponse)
async def revoke_invitation(invitation_id: UUID, user: User = Depends(require_invitation_manager), db: AsyncSession = Depends(get_db)) -> InvitationResponse:
    invitation = await db.scalar(select(StaffInvitation).where(StaffInvitation.id == invitation_id, StaffInvitation.clinic_id == user.clinic_id).with_for_update())
    if not invitation:
        raise HTTPException(404, "Invitation not found")
    if invitation.accepted_at is not None:
        raise HTTPException(409, "Accepted invitations cannot be revoked")
    invitation.revoked_at = utcnow()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="staff_invitation", entity_id=invitation.id, action="revoked"))
    await db.commit()
    await db.refresh(invitation)
    return invitation_response(invitation)


@router.post("/invitations/{invitation_id}/reissue", response_model=InvitationResponse)
async def reissue_invitation(invitation_id: UUID, user: User = Depends(require_invitation_manager), db: AsyncSession = Depends(get_db)) -> InvitationResponse:
    old = await db.scalar(select(StaffInvitation).where(StaffInvitation.id == invitation_id, StaffInvitation.clinic_id == user.clinic_id).with_for_update())
    if not old:
        raise HTTPException(404, "Invitation not found")
    if old.accepted_at is not None:
        raise HTTPException(409, "Accepted invitations cannot be reissued")
    old.revoked_at = utcnow()
    raw = secrets.token_urlsafe(48)
    invitation = StaffInvitation(clinic_id=user.clinic_id, invited_by_id=user.id, email=old.email, full_name=old.full_name, role=old.role, token_hash=token_hash(raw), expires_at=utcnow() + timedelta(days=INVITATION_DAYS))
    db.add(invitation)
    await db.flush()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="staff_invitation", entity_id=invitation.id, action="reissued"))
    await db.commit()
    await db.refresh(invitation)
    return invitation_response(invitation, raw)


@router.post("/invitations/accept", response_model=StaffResponse)
async def accept_invitation(token: str, payload: InvitationAccept, db: AsyncSession = Depends(get_db)) -> StaffResponse:
    invitation = await db.scalar(select(StaffInvitation).where(StaffInvitation.token_hash == token_hash(token)).with_for_update())
    now = utcnow()
    if not invitation or invitation.revoked_at is not None or invitation.accepted_at is not None or aware(invitation.expires_at) <= now:
        raise HTTPException(400, "Invitation is invalid, expired, revoked, or already used")
    if await db.scalar(select(User).where(User.email == invitation.email)):
        raise HTTPException(409, "An account already exists for this email")
    user = User(clinic_id=invitation.clinic_id, full_name=invitation.full_name, email=invitation.email, password_hash=hash_password(payload.password), role=invitation.role, is_active=True)
    db.add(user)
    invitation.accepted_at = now
    await db.flush()
    db.add(AuditEvent(clinic_id=invitation.clinic_id, actor_id=user.id, entity_type="staff", entity_id=user.id, action="created_from_invitation"))
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/{staff_id}/deactivate", response_model=StaffStatusResponse)
async def deactivate_staff(staff_id: UUID, user: User = Depends(require_invitation_manager), db: AsyncSession = Depends(get_db)) -> StaffStatusResponse:
    staff = await db.scalar(select(User).where(User.id == staff_id, User.clinic_id == user.clinic_id).with_for_update())
    if not staff:
        raise HTTPException(404, "Staff member not found")
    if staff.role in MANAGEMENT_ROLES:
        raise HTTPException(409, "Manager and administrator accounts cannot be deactivated here")
    affected = (await db.scalars(select(Appointment).where(Appointment.clinic_id == user.clinic_id, Appointment.starts_at > utcnow(), Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES), (Appointment.doctor_id == staff.id) | (Appointment.assistant_id == staff.id)).order_by(Appointment.starts_at))).all()
    if affected:
        return StaffStatusResponse(staff=staff, affected_appointments=[{"id": str(item.id), "starts_at": item.starts_at.isoformat(), "status": item.status} for item in affected])
    staff.is_active = False
    staff.deactivated_at = utcnow()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="staff", entity_id=staff.id, action="deactivated"))
    await db.commit()
    await db.refresh(staff)
    return StaffStatusResponse(staff=staff, affected_appointments=[])

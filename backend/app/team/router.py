"""T19 team: profiles, protected administrative documents, shifts, leave, attendance, schedule.

Access rules enforced here:
- Administrative documents are visible only to a manager and to the person concerned. Anyone else
  gets the same 404 as for a missing document, so a document id reveals nothing.
- Leave type and reason are visible only to a manager and to the person concerned.
- Attendance is separate from procedure timers and is never derived from them.
- Approved leave is written to ``resource_unavailability``, the only source of truth for the
  agenda. Existing appointments are reported, never moved or blocked automatically.

Lock order: user row, then room row (the same order used by appointment validation).
"""

import asyncio
from datetime import date, datetime, timedelta
from typing import Literal
from urllib.parse import quote, unquote
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointments.service import (
    ACTIVE_STATUSES,
    clinic_timezone,
    local_bounds,
    local_to_utc,
    utc,
)
from app.auth.service import get_current_user, require_roles
from app.config import settings
from app.database import (
    Appointment,
    AuditEvent,
    ResourceUnavailability,
    Room,
    User,
    get_db,
    utcnow,
)
from app.documents import storage
from app.documents.router import SECURITY_HEADERS
from app.team.models import (
    TeamAttendance,
    TeamAttendanceCorrection,
    TeamDocument,
    TeamLeaveRequest,
    TeamProfile,
    TeamShift,
)

router = APIRouter(prefix="/team", tags=["team"])
MANAGERS = ("clinic_manager", "administrator")
MAX_SHIFT = timedelta(hours=24)
MAX_ATTENDANCE = timedelta(hours=24)
MAX_LEAVE_DAYS = 366
LEAVE_AGENDA_LABEL = "Concediu"  # all staff can read unavailability: never the type or reason


def is_manager(user: User) -> bool:
    return user.role in MANAGERS


def iso(value: datetime | None) -> str | None:
    return None if value is None else utc(value).isoformat().replace("+00:00", "Z")


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Motivul este obligatoriu")
        return value


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str | None = Field(default=None, max_length=40)
    emergency_contact_name: str | None = Field(default=None, max_length=160)
    emergency_contact_phone: str | None = Field(default=None, max_length=40)
    job_title: str | None = Field(default=None, max_length=120)
    specialization: str | None = Field(default=None, max_length=160)
    employment_start: date | None = None


class ShiftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    room_id: UUID | None = None
    local_start: datetime
    local_end: datetime
    start_fold: int | None = Field(default=None, ge=0, le=1)
    end_fold: int | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def naive_local(self) -> "ShiftCreate":
        if self.local_start.tzinfo is not None or self.local_end.tzinfo is not None:
            raise ValueError("Orele turei se introduc în fusul clinicii, fără offset")
        return self


class LeaveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID | None = None
    leave_type: Literal["annual", "medical", "unpaid", "other"]
    starts_on: date
    ends_on: date
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def valid_interval(self) -> "LeaveCreate":
        if self.ends_on < self.starts_on:
            raise ValueError("Sfârșitul concediului nu poate fi înaintea începutului")
        if (self.ends_on - self.starts_on).days + 1 > MAX_LEAVE_DAYS:
            raise ValueError("Concediul depășește durata maximă")
        return self


class CorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clock_in_at: datetime | None = None
    clock_out_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Motivul este obligatoriu")
        return value

    @model_validator(mode="after")
    def valid(self) -> "CorrectionCreate":
        if self.clock_in_at is None and self.clock_out_at is None:
            raise ValueError("Corecția trebuie să modifice intrarea sau ieșirea")
        for value in (self.clock_in_at, self.clock_out_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("Momentele trebuie să includă fusul orar")
        return self


class ManualAttendance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    clock_in_at: datetime
    clock_out_at: datetime
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Motivul este obligatoriu")
        return value

    @model_validator(mode="after")
    def aware(self) -> "ManualAttendance":
        if self.clock_in_at.tzinfo is None or self.clock_out_at.tzinfo is None:
            raise ValueError("Momentele trebuie să includă fusul orar")
        return self


def audit(db: AsyncSession, user_clinic: UUID, actor: UUID, kind: str, entity: UUID, action: str) -> None:
    db.add(AuditEvent(clinic_id=user_clinic, actor_id=actor, entity_type=kind, entity_id=entity, action=action))


async def member(db: AsyncSession, clinic_id: UUID, user_id: UUID, *, lock: bool = False) -> User:
    query = select(User).where(User.id == user_id, User.clinic_id == clinic_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    person = await db.scalar(query)
    if not person:
        raise HTTPException(404, "Persoana nu a fost găsită")
    return person


async def names(db: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str]:
    wanted = {value for value in ids if value}
    if not wanted:
        return {}
    return {row[0]: row[1] for row in (await db.execute(select(User.id, User.full_name).where(User.id.in_(wanted)))).all()}


# ---------------------------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------------------------


def profile_body(person: User, profile: TeamProfile | None, viewer: User) -> dict:
    body = {
        "id": str(person.id), "full_name": person.full_name, "role": person.role, "is_active": person.is_active,
        "specialization": person.specialization, "job_title": profile.job_title if profile else None,
    }
    if is_manager(viewer) or viewer.id == person.id:
        body |= {
            "email": person.email, "phone": person.phone,
            "employment_start": profile.employment_start.isoformat() if profile and profile.employment_start else None,
            "emergency_contact_name": profile.emergency_contact_name if profile else None,
            "emergency_contact_phone": profile.emergency_contact_phone if profile else None,
        }
    return body


@router.get("/members")
async def list_members(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    people = (await db.scalars(select(User).where(User.clinic_id == user.clinic_id).order_by(User.full_name))).all()
    profiles = {row.user_id: row for row in await db.scalars(select(TeamProfile).where(TeamProfile.clinic_id == user.clinic_id))}
    return [profile_body(person, profiles.get(person.id), user) for person in people]


@router.get("/members/{user_id}/profile")
async def get_profile(user_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    person = await member(db, user.clinic_id, user_id)
    profile = await db.scalar(select(TeamProfile).where(TeamProfile.user_id == person.id, TeamProfile.clinic_id == user.clinic_id))
    return profile_body(person, profile, user)


@router.put("/members/{user_id}/profile")
async def update_profile(user_id: UUID, payload: ProfileUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    person = await member(db, user.clinic_id, user_id, lock=True)
    if not is_manager(user) and person.id != user.id:
        raise HTTPException(403, "Poți modifica doar propriul profil")
    data = payload.model_dump(exclude_unset=True)
    if not is_manager(user) and set(data) & {"job_title", "specialization", "employment_start"}:
        raise HTTPException(403, "Funcția, specializarea și data angajării se modifică de manager")
    profile = await db.scalar(select(TeamProfile).where(TeamProfile.user_id == person.id, TeamProfile.clinic_id == user.clinic_id))
    if profile is None:
        profile = TeamProfile(user_id=person.id, clinic_id=user.clinic_id, updated_by_id=user.id)
        db.add(profile)
    for key, value in data.items():
        value = clean(value) if isinstance(value, str) else value
        if key in {"phone", "specialization"}:
            setattr(person, key, value)
        else:
            setattr(profile, key, value)
    profile.updated_by_id = user.id
    profile.updated_at = utcnow()
    audit(db, user.clinic_id, user.id, "team_profile", person.id, "updated")
    await db.commit()
    return profile_body(person, profile, user)


# ---------------------------------------------------------------------------------------------
# Protected administrative documents (T11 storage, unchanged)
# ---------------------------------------------------------------------------------------------


def document_body(item: TeamDocument, people: dict[UUID, str]) -> dict:
    return {
        "id": str(item.id), "user_id": str(item.user_id), "category": item.category, "description": item.description,
        "original_filename": item.original_filename, "content_type": item.content_type, "size_bytes": item.size_bytes,
        "expires_on": item.expires_on.isoformat() if item.expires_on else None, "status": item.status,
        "uploaded_at": iso(item.uploaded_at), "uploaded_by": people.get(item.uploaded_by_id, "—"),
        "archived_at": iso(item.archived_at), "archived_by": people.get(item.archived_by_id) if item.archived_by_id else None,
        "archive_reason": item.archive_reason,
    }


async def readable_document(db: AsyncSession, document_id: UUID, user: User, *, lock: bool = False) -> TeamDocument:
    query = select(TeamDocument).where(TeamDocument.id == document_id, TeamDocument.clinic_id == user.clinic_id)
    if lock:
        query = query.with_for_update()
    item = await db.scalar(query)
    if not item or not (is_manager(user) or item.user_id == user.id):
        raise HTTPException(404, "Document inexistent sau fără acces")
    return item


def decode_header(raw: str | None, name: str, limit: int) -> str | None:
    if raw is None:
        return None
    try:
        value = unquote(raw, encoding="utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise HTTPException(422, f"{name} trebuie codificat UTF-8") from exc
    if len(value) > limit:
        raise HTTPException(422, f"{name} este prea lung")
    return value or None


def idempotency(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(UUID(raw))
    except ValueError as exc:
        raise HTTPException(422, "Idempotency-Key trebuie să fie UUID") from exc


@router.get("/members/{user_id}/documents")
async def list_documents(user_id: UUID, include_archived: bool = False, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    person = await member(db, user.clinic_id, user_id)
    if not is_manager(user) and person.id != user.id:
        raise HTTPException(403, "Documentele sunt vizibile doar managerului și persoanei în cauză")
    query = select(TeamDocument).where(TeamDocument.clinic_id == user.clinic_id, TeamDocument.user_id == person.id)
    if not include_archived:
        query = query.where(TeamDocument.status == "active")
    items = list(await db.scalars(query.order_by(TeamDocument.uploaded_at.desc(), TeamDocument.id)))
    people = await names(db, {i.uploaded_by_id for i in items} | {i.archived_by_id for i in items})
    return [document_body(item, people) for item in items]


@router.post("/members/{user_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    user_id: UUID,
    request: Request,
    category: Literal["contract", "certificate", "medical", "other"] = Query(...),
    expires_on: date | None = Query(default=None),
    file_name: str | None = Header(default=None, alias="X-File-Name"),
    description_header: str | None = Header(default=None, alias="X-Document-Description"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    content_length: int | None = Header(default=None, alias="Content-Length"),
    user: User = Depends(require_roles(*MANAGERS)),
    db: AsyncSession = Depends(get_db),
):
    display_name = storage.safe_display_name(decode_header(file_name, "X-File-Name", 1024))
    description = decode_header(description_header, "X-Document-Description", 500)
    key = idempotency(idempotency_key)
    max_bytes = settings.document_max_bytes
    if content_length is not None and content_length > max_bytes:
        raise HTTPException(413, f"Fișierul depășește limita de {max_bytes // (1024 * 1024)} MiB")
    clinic_id, actor_id = user.clinic_id, user.id
    await member(db, clinic_id, user_id)
    await db.rollback()  # do not hold a connection while the body streams

    staged = None
    final_path = None
    try:
        try:
            staged = await storage.stage_upload(request.stream(), max_bytes)
            await asyncio.to_thread(storage.inspect_content, staged)
            storage.check_extension(display_name, staged.content_type)
        except storage.ContentRejected as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

        async def existing_retry() -> TeamDocument | None:
            return await db.scalar(select(TeamDocument).where(
                TeamDocument.clinic_id == clinic_id, TeamDocument.uploaded_by_id == actor_id,
                TeamDocument.idempotency_key == key))

        if key and (previous := await existing_retry()):
            if previous.user_id != user_id or previous.sha256 != staged.sha256:
                raise HTTPException(409, "Idempotency-Key a fost folosit pentru alt fișier")
            return document_body(previous, await names(db, {previous.uploaded_by_id}))
        await member(db, clinic_id, user_id, lock=True)
        storage_key = storage.new_storage_key()
        item = TeamDocument(
            clinic_id=clinic_id, user_id=user_id, category=category, description=description,
            original_filename=display_name, content_type=staged.content_type, size_bytes=staged.size,
            sha256=staged.sha256, storage_key=storage_key, expires_on=expires_on, uploaded_by_id=actor_id,
            uploaded_at=utcnow(), idempotency_key=key, status="active")
        db.add(item)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            if key and (previous := await existing_retry()) and previous.user_id == user_id and previous.sha256 == staged.sha256:
                return document_body(previous, await names(db, {previous.uploaded_by_id}))
            raise HTTPException(409, "Documentul nu a putut fi salvat; reîncearcă") from exc
        audit(db, clinic_id, actor_id, "team_document", item.id, "uploaded")
        try:
            final_path = storage.place(staged, clinic_id, storage_key)
        except OSError as exc:
            raise storage.StorageUnavailable("Documentul nu a putut fi stocat") from exc
        await db.commit()
        final_path = None
        return document_body(item, await names(db, {actor_id}))
    except storage.StorageUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        if final_path is not None:
            await db.rollback()
            storage.discard(final_path)
        if staged is not None:
            storage.discard(staged.path)


@router.get("/documents/{document_id}/content")
async def document_content(document_id: UUID, download: bool = False, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    item = await readable_document(db, document_id, user)
    try:
        path = storage.content_path(item.clinic_id, item.storage_key)
    except storage.StorageUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, "Conținutul documentului nu este disponibil")
    audit(db, item.clinic_id, user.id, "team_document", item.id, "downloaded" if download else "viewed")
    await db.commit()
    disposition = "attachment" if download or item.content_type == "application/pdf" else "inline"
    ascii_name = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._- ") else "_" for ch in item.original_filename)
    headers = {**SECURITY_HEADERS, "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(item.original_filename)}"}
    return FileResponse(path, media_type=item.content_type, headers=headers)


@router.post("/documents/{document_id}/archive")
async def archive_document(document_id: UUID, payload: ReasonBody, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    item = await readable_document(db, document_id, user, lock=True)
    if item.status == "archived":
        raise HTTPException(409, "Documentul este deja arhivat")
    item.status, item.archived_at, item.archived_by_id, item.archive_reason = "archived", utcnow(), user.id, payload.reason
    audit(db, user.clinic_id, user.id, "team_document", item.id, "archived")
    await db.commit()
    return document_body(item, await names(db, {item.uploaded_by_id, item.archived_by_id}))


# ---------------------------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------------------------


def post_for(person: User, room_id: UUID | None) -> str:
    if person.role == "assistant":
        post = "assistant"
    elif person.role == "doctor":
        post = "doctor"
    elif person.role in MANAGERS and room_id is not None:
        post = "doctor"  # managers can be booked as doctors by the agenda
    else:
        post = "front_desk"
    if post == "front_desk" and room_id is not None:
        raise HTTPException(422, "Recepția nu se repartizează pe cabinet")
    if post != "front_desk" and room_id is None:
        raise HTTPException(422, "Medicul și asistentul trebuie repartizați pe un cabinet")
    return post


def shift_body(item: TeamShift, people: dict[UUID, str], rooms: dict[UUID, str], zone: ZoneInfo) -> dict:
    return {
        "id": str(item.id), "user_id": str(item.user_id), "user_name": people.get(item.user_id, "—"),
        "room_id": str(item.room_id) if item.room_id else None, "room_name": rooms.get(item.room_id) if item.room_id else None,
        "post": item.post, "starts_at": iso(item.starts_at), "ends_at": iso(item.ends_at),
        "local_start": utc(item.starts_at).astimezone(zone).isoformat(), "local_end": utc(item.ends_at).astimezone(zone).isoformat(),
        "status": item.status,
    }


async def room_names(db: AsyncSession, clinic_id: UUID) -> dict[UUID, str]:
    return {row[0]: row[1] for row in (await db.execute(select(Room.id, Room.name).where(Room.clinic_id == clinic_id))).all()}


@router.post("/shifts", status_code=status.HTTP_201_CREATED)
async def create_shift(payload: ShiftCreate, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    clinic, zone = await clinic_timezone(db, user.clinic_id)
    start = local_to_utc(payload.local_start, clinic.timezone, payload.start_fold)
    end = local_to_utc(payload.local_end, clinic.timezone, payload.end_fold)
    if end <= start:
        raise HTTPException(422, "Sfârșitul turei trebuie să fie după început")
    if end - start > MAX_SHIFT:
        raise HTTPException(422, "O tură nu poate depăși 24 de ore")
    person = await member(db, user.clinic_id, payload.user_id, lock=True)
    if not person.is_active:
        raise HTTPException(422, "Persoana este inactivă")
    post = post_for(person, payload.room_id)
    if payload.room_id is not None:
        room = await db.scalar(select(Room).where(Room.id == payload.room_id, Room.clinic_id == user.clinic_id, Room.is_active.is_(True)).with_for_update())
        if not room:
            raise HTTPException(422, "Cabinetul nu este valid pentru această clinică")
    overlap = TeamShift.starts_at < end, TeamShift.ends_at > start
    active = TeamShift.clinic_id == user.clinic_id, TeamShift.status == "active"
    if await db.scalar(select(TeamShift.id).where(*active, *overlap, TeamShift.user_id == person.id).limit(1)):
        raise HTTPException(409, "Persoana are deja o tură în acest interval")
    if payload.room_id is not None and await db.scalar(select(TeamShift.id).where(*active, *overlap, TeamShift.room_id == payload.room_id, TeamShift.post == post).limit(1)):
        raise HTTPException(409, "Cabinetul are deja repartizată o persoană pe acest post în interval")
    local_first = start.astimezone(zone).date()
    local_last = (end - timedelta(microseconds=1)).astimezone(zone).date()
    if await db.scalar(select(TeamLeaveRequest.id).where(
            TeamLeaveRequest.clinic_id == user.clinic_id, TeamLeaveRequest.user_id == person.id, TeamLeaveRequest.status == "approved",
            TeamLeaveRequest.starts_on <= local_last, TeamLeaveRequest.ends_on >= local_first).limit(1)):
        raise HTTPException(409, "Persoana are concediu aprobat în acest interval")
    item = TeamShift(clinic_id=user.clinic_id, user_id=person.id, room_id=payload.room_id, post=post, starts_at=start, ends_at=end, status="active", created_by_id=user.id)
    db.add(item)
    try:
        await db.flush()
        audit(db, user.clinic_id, user.id, "team_shift", item.id, "created")
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Tura se suprapune cu o altă tură") from exc
    return shift_body(item, {person.id: person.full_name}, await room_names(db, user.clinic_id), zone)


@router.post("/shifts/{shift_id}/cancel")
async def cancel_shift(shift_id: UUID, payload: ReasonBody, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    _, zone = await clinic_timezone(db, user.clinic_id)
    item = await db.scalar(select(TeamShift).where(TeamShift.id == shift_id, TeamShift.clinic_id == user.clinic_id).with_for_update())
    if not item:
        raise HTTPException(404, "Tura nu a fost găsită")
    if item.status != "active":
        raise HTTPException(409, "Tura este deja anulată")
    item.status, item.cancelled_at, item.cancelled_by_id, item.cancel_reason = "cancelled", utcnow(), user.id, payload.reason
    audit(db, user.clinic_id, user.id, "team_shift", item.id, "cancelled")
    await db.commit()
    return shift_body(item, await names(db, {item.user_id}), await room_names(db, user.clinic_id), zone)


# ---------------------------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------------------------


def agenda_resource(person: User) -> str | None:
    """Agenda resource type for this person; reception is not a bookable agenda resource."""
    if person.role == "assistant":
        return "assistant"
    if person.role == "doctor" or person.role in MANAGERS:
        return "doctor"
    return None


async def leave_body(db: AsyncSession, item: TeamLeaveRequest, viewer: User, people: dict[UUID, str]) -> dict:
    blocked = bool(item.unavailability_id and await db.scalar(select(ResourceUnavailability.id).where(
        ResourceUnavailability.id == item.unavailability_id, ResourceUnavailability.clinic_id == item.clinic_id)))
    body = {
        "id": str(item.id), "user_id": str(item.user_id), "user_name": people.get(item.user_id, "—"),
        "starts_on": item.starts_on.isoformat(), "ends_on": item.ends_on.isoformat(), "status": item.status,
        "agenda_blocked": blocked,
    }
    if is_manager(viewer) or viewer.id == item.user_id:
        body |= {
            "leave_type": item.leave_type, "reason": item.reason, "decision_reason": item.decision_reason,
            "decided_by": people.get(item.decided_by_id) if item.decided_by_id else None, "decided_at": iso(item.decided_at),
            "created_at": iso(item.created_at),
        }
    return body


@router.get("/leave")
async def list_leave(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    query = select(TeamLeaveRequest).where(TeamLeaveRequest.clinic_id == user.clinic_id)
    if not is_manager(user):
        query = query.where(TeamLeaveRequest.user_id == user.id)
    items = list(await db.scalars(query.order_by(TeamLeaveRequest.starts_on.desc(), TeamLeaveRequest.id)))
    people = await names(db, {i.user_id for i in items} | {i.decided_by_id for i in items})
    return [await leave_body(db, item, user, people) for item in items]


@router.post("/leave", status_code=status.HTTP_201_CREATED)
async def create_leave(payload: LeaveCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    target = payload.user_id or user.id
    if target != user.id and not is_manager(user):
        raise HTTPException(403, "Poți cere concediu doar pentru tine")
    person = await member(db, user.clinic_id, target, lock=True)
    if not person.is_active:
        raise HTTPException(422, "Persoana este inactivă")
    clash = await db.scalar(select(TeamLeaveRequest.id).where(
        TeamLeaveRequest.clinic_id == user.clinic_id, TeamLeaveRequest.user_id == person.id,
        TeamLeaveRequest.status.in_(("pending", "approved")),
        TeamLeaveRequest.starts_on <= payload.ends_on, TeamLeaveRequest.ends_on >= payload.starts_on).limit(1))
    if clash:
        raise HTTPException(409, "Există deja o cerere de concediu în acest interval")
    item = TeamLeaveRequest(clinic_id=user.clinic_id, user_id=person.id, leave_type=payload.leave_type, starts_on=payload.starts_on,
                            ends_on=payload.ends_on, reason=clean(payload.reason), status="pending", requested_by_id=user.id)
    db.add(item)
    await db.flush()
    audit(db, user.clinic_id, user.id, "team_leave", item.id, "requested")
    await db.commit()
    return await leave_body(db, item, user, {person.id: person.full_name})


async def locked_leave(db: AsyncSession, leave_id: UUID, user: User) -> tuple[TeamLeaveRequest, User]:
    owner = await db.scalar(select(TeamLeaveRequest.user_id).where(TeamLeaveRequest.id == leave_id, TeamLeaveRequest.clinic_id == user.clinic_id))
    if owner is None or not (is_manager(user) or owner == user.id):
        raise HTTPException(404, "Cererea de concediu nu a fost găsită")
    person = await member(db, user.clinic_id, owner, lock=True)
    item = await db.scalar(select(TeamLeaveRequest).where(TeamLeaveRequest.id == leave_id, TeamLeaveRequest.clinic_id == user.clinic_id)
                           .with_for_update().execution_options(populate_existing=True))
    return item, person


@router.post("/leave/{leave_id}/approve")
async def approve_leave(leave_id: UUID, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    item, person = await locked_leave(db, leave_id, user)
    if item.status != "pending":
        raise HTTPException(409, "Doar o cerere în așteptare poate fi aprobată")
    clinic, _ = await clinic_timezone(db, user.clinic_id)
    start = local_bounds(item.starts_on, clinic.timezone)[0]
    end = local_bounds(item.ends_on, clinic.timezone)[1]
    kind = agenda_resource(person)
    affected: list[Appointment] = []
    if kind:
        block = ResourceUnavailability(clinic_id=user.clinic_id, resource_type=kind, resource_id=person.id, starts_at=start, ends_at=end,
                                       reason=LEAVE_AGENDA_LABEL, created_by_id=user.id)
        db.add(block)
        await db.flush()
        item.unavailability_id = block.id
        column = Appointment.doctor_id if kind == "doctor" else Appointment.assistant_id
        affected = list(await db.scalars(select(Appointment).where(
            Appointment.clinic_id == user.clinic_id, Appointment.status.in_(ACTIVE_STATUSES), column == person.id,
            Appointment.starts_at < end, Appointment.ends_at > start).order_by(Appointment.starts_at)))
    shifts = list(await db.scalars(select(TeamShift).where(
        TeamShift.clinic_id == user.clinic_id, TeamShift.user_id == person.id, TeamShift.status == "active",
        TeamShift.starts_at < end, TeamShift.ends_at > start).order_by(TeamShift.starts_at)))
    item.status, item.decided_by_id, item.decided_at = "approved", user.id, utcnow()
    audit(db, user.clinic_id, user.id, "team_leave", item.id, "approved")
    affected_appointments = [{"id": str(a.id), "starts_at": iso(a.starts_at), "status": a.status} for a in affected]
    affected_shifts = [{"id": str(s.id), "starts_at": iso(s.starts_at), "ends_at": iso(s.ends_at)} for s in shifts]
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Concediul se suprapune cu un alt concediu aprobat") from exc
    body = await leave_body(db, item, user, await names(db, {item.user_id, item.decided_by_id}))
    return {**body, "affected_appointments": affected_appointments, "affected_shifts": affected_shifts}


@router.post("/leave/{leave_id}/reject")
async def reject_leave(leave_id: UUID, payload: ReasonBody, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    item, _ = await locked_leave(db, leave_id, user)
    if item.status != "pending":
        raise HTTPException(409, "Doar o cerere în așteptare poate fi respinsă")
    item.status, item.decided_by_id, item.decided_at, item.decision_reason = "rejected", user.id, utcnow(), payload.reason
    audit(db, user.clinic_id, user.id, "team_leave", item.id, "rejected")
    await db.commit()
    return await leave_body(db, item, user, await names(db, {item.user_id, item.decided_by_id}))


@router.post("/leave/{leave_id}/cancel")
async def cancel_leave(leave_id: UUID, payload: ReasonBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    item, _ = await locked_leave(db, leave_id, user)
    if item.status == "approved" and not is_manager(user):
        raise HTTPException(403, "Un concediu aprobat poate fi anulat doar de manager")
    if item.status not in ("pending", "approved"):
        raise HTTPException(409, "Cererea nu mai poate fi anulată")
    if item.unavailability_id:
        block = await db.scalar(select(ResourceUnavailability).where(
            ResourceUnavailability.id == item.unavailability_id, ResourceUnavailability.clinic_id == user.clinic_id).with_for_update())
        item.unavailability_id = None
        if block:
            await db.flush()
            await db.delete(block)
    item.status, item.decided_by_id, item.decided_at, item.decision_reason = "cancelled", user.id, utcnow(), payload.reason
    audit(db, user.clinic_id, user.id, "team_leave", item.id, "cancelled")
    await db.commit()
    return await leave_body(db, item, user, await names(db, {item.user_id, item.decided_by_id}))


# ---------------------------------------------------------------------------------------------
# Attendance (never connected to procedure timers)
# ---------------------------------------------------------------------------------------------


async def attendance_rows(db: AsyncSession, sessions: list[TeamAttendance]) -> list[dict]:
    ids = [row.id for row in sessions]
    corrections: dict[UUID, list[TeamAttendanceCorrection]] = {}
    if ids:
        for row in await db.scalars(select(TeamAttendanceCorrection).where(TeamAttendanceCorrection.attendance_id.in_(ids)).order_by(TeamAttendanceCorrection.revision)):
            corrections.setdefault(row.attendance_id, []).append(row)
    people = await names(db, {s.user_id for s in sessions} | {c.created_by_id for rows in corrections.values() for c in rows})
    result = []
    for session in sessions:
        history = corrections.get(session.id, [])
        effective_in, effective_out = effective(session, history)
        result.append({
            "id": str(session.id), "user_id": str(session.user_id), "user_name": people.get(session.user_id, "—"), "source": session.source,
            "clock_in_at": iso(session.clock_in_at), "clock_out_at": iso(session.clock_out_at),
            "effective_clock_in_at": iso(effective_in), "effective_clock_out_at": iso(effective_out), "is_open": session.clock_out_at is None,
            "worked_seconds": int((effective_out - effective_in).total_seconds()) if effective_out else None,
            "corrections": [{"revision": c.revision, "clock_in_at": iso(c.clock_in_at), "clock_out_at": iso(c.clock_out_at), "reason": c.reason,
                             "created_by": people.get(c.created_by_id, "—"), "created_at": iso(c.created_at)} for c in history],
        })
    return result


def effective(session: TeamAttendance, history: list[TeamAttendanceCorrection]) -> tuple[datetime, datetime | None]:
    if not history:
        return utc(session.clock_in_at), utc(session.clock_out_at) if session.clock_out_at else None
    last = history[-1]
    out = last.clock_out_at or session.clock_out_at
    return utc(last.clock_in_at), utc(out) if out else None


async def open_session(db: AsyncSession, clinic_id: UUID, user_id: UUID, *, lock: bool = False) -> TeamAttendance | None:
    query = select(TeamAttendance).where(TeamAttendance.clinic_id == clinic_id, TeamAttendance.user_id == user_id, TeamAttendance.clock_out_at.is_(None))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(query)


@router.get("/attendance/me")
async def my_attendance(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    sessions = list(await db.scalars(select(TeamAttendance).where(TeamAttendance.clinic_id == user.clinic_id, TeamAttendance.user_id == user.id).order_by(TeamAttendance.clock_in_at.desc()).limit(30)))
    rows = await attendance_rows(db, sessions)
    return {"open": next((row for row in rows if row["is_open"]), None), "sessions": rows}


@router.post("/attendance/clock-in", status_code=status.HTTP_201_CREATED)
async def clock_in(request_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    clinic_id, user_id = request_user.clinic_id, request_user.id
    await member(db, clinic_id, user_id, lock=True)
    existing = await open_session(db, clinic_id, user_id)
    if existing:  # double click or second tab: same open session, nothing new
        return JSONResponse((await attendance_rows(db, [existing]))[0], status_code=200)
    item = TeamAttendance(clinic_id=clinic_id, user_id=user_id, source="clock", clock_in_at=utcnow(), created_by_id=user_id)
    db.add(item)
    try:
        await db.flush()
        audit(db, clinic_id, user_id, "team_attendance", item.id, "clock_in")
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await open_session(db, clinic_id, user_id)
        if existing:
            return JSONResponse((await attendance_rows(db, [existing]))[0], status_code=200)
        raise HTTPException(409, "Prezența nu a putut fi înregistrată; reîncearcă") from None
    return (await attendance_rows(db, [item]))[0]


@router.post("/attendance/clock-out")
async def clock_out(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    clinic_id, user_id = user.clinic_id, user.id
    await member(db, clinic_id, user_id, lock=True)
    item = await open_session(db, clinic_id, user_id, lock=True)
    if not item:
        raise HTTPException(409, "Nu ai o prezență deschisă")
    # The smallest step keeps the interval valid if the system clock moved back after clock-in.
    item.clock_out_at = max(utcnow(), utc(item.clock_in_at) + timedelta(microseconds=1))
    item.closed_by_id = user_id
    audit(db, clinic_id, user_id, "team_attendance", item.id, "clock_out")
    await db.commit()
    return (await attendance_rows(db, [item]))[0]


@router.get("/attendance")
async def list_attendance(
    start: datetime = Query(...), end: datetime = Query(...), user_id: UUID | None = None,
    user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db),
) -> list[dict]:
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise HTTPException(422, "Interval invalid")
    query = select(TeamAttendance).where(TeamAttendance.clinic_id == user.clinic_id, TeamAttendance.clock_in_at < end,
                                         (TeamAttendance.clock_out_at.is_(None)) | (TeamAttendance.clock_out_at > start))
    if user_id:
        await member(db, user.clinic_id, user_id)
        query = query.where(TeamAttendance.user_id == user_id)
    return await attendance_rows(db, list(await db.scalars(query.order_by(TeamAttendance.clock_in_at.desc()).limit(500))))


async def assert_no_overlap(db: AsyncSession, clinic_id: UUID, user_id: UUID, start: datetime, end: datetime | None, exclude: UUID | None) -> None:
    query = select(TeamAttendance).where(TeamAttendance.clinic_id == clinic_id, TeamAttendance.user_id == user_id)
    if exclude:
        query = query.where(TeamAttendance.id != exclude)
    sessions = list(await db.scalars(query))
    corrections: dict[UUID, list[TeamAttendanceCorrection]] = {}
    if sessions:
        for row in await db.scalars(select(TeamAttendanceCorrection).where(TeamAttendanceCorrection.attendance_id.in_([s.id for s in sessions])).order_by(TeamAttendanceCorrection.revision)):
            corrections.setdefault(row.attendance_id, []).append(row)
    for session in sessions:
        other_in, other_out = effective(session, corrections.get(session.id, []))
        if (end is None or other_in < end) and (other_out is None or other_out > start):
            raise HTTPException(409, "Intervalul se suprapune cu o altă prezență a persoanei")


@router.post("/attendance/{attendance_id}/corrections", status_code=status.HTTP_201_CREATED)
async def correct_attendance(attendance_id: UUID, payload: CorrectionCreate, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    owner = await db.scalar(select(TeamAttendance.user_id).where(TeamAttendance.id == attendance_id, TeamAttendance.clinic_id == user.clinic_id))
    if owner is None:
        raise HTTPException(404, "Prezența nu a fost găsită")
    await member(db, user.clinic_id, owner, lock=True)
    item = await db.scalar(select(TeamAttendance).where(TeamAttendance.id == attendance_id).with_for_update().execution_options(populate_existing=True))
    history = list(await db.scalars(select(TeamAttendanceCorrection).where(TeamAttendanceCorrection.attendance_id == item.id).order_by(TeamAttendanceCorrection.revision)))
    current_in, current_out = effective(item, history)
    new_in = utc(payload.clock_in_at) if payload.clock_in_at else current_in
    new_out = utc(payload.clock_out_at) if payload.clock_out_at else current_out
    now = utcnow()
    if any(value is not None and utc(value) > now for value in (payload.clock_in_at, payload.clock_out_at)):
        raise HTTPException(422, "Prezența nu poate fi corectată în viitor")
    if new_out is not None and new_out <= new_in:
        raise HTTPException(422, "Ieșirea trebuie să fie după intrare")
    if new_out is not None and new_out - new_in > MAX_ATTENDANCE:
        raise HTTPException(422, "O prezență nu poate depăși 24 de ore")
    if item.clock_out_at is None and new_out is not None and new_out <= utc(item.clock_in_at):
        raise HTTPException(422, "Ieșirea unei prezențe deschise trebuie să fie după intrarea înregistrată")
    await assert_no_overlap(db, user.clinic_id, owner, new_in, new_out, item.id)
    if item.clock_out_at is None and new_out is not None:
        item.clock_out_at = new_out  # first close of an open session, by the manager
        item.closed_by_id = user.id
    revision = (history[-1].revision if history else 0) + 1
    db.add(TeamAttendanceCorrection(clinic_id=user.clinic_id, attendance_id=item.id, revision=revision, clock_in_at=new_in, clock_out_at=new_out, reason=payload.reason, created_by_id=user.id))
    audit(db, user.clinic_id, user.id, "team_attendance", item.id, "corrected")
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Prezența a fost modificată între timp; reîncearcă") from exc
    return (await attendance_rows(db, [item]))[0]


@router.post("/attendance/manual", status_code=status.HTTP_201_CREATED)
async def manual_attendance(payload: ManualAttendance, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    start, end = utc(payload.clock_in_at), utc(payload.clock_out_at)
    if end <= start:
        raise HTTPException(422, "Ieșirea trebuie să fie după intrare")
    if end > utcnow():
        raise HTTPException(422, "Prezența nu poate fi adăugată în viitor")
    if end - start > MAX_ATTENDANCE:
        raise HTTPException(422, "O prezență nu poate depăși 24 de ore")
    person = await member(db, user.clinic_id, payload.user_id, lock=True)
    await assert_no_overlap(db, user.clinic_id, person.id, start, end, None)
    item = TeamAttendance(clinic_id=user.clinic_id, user_id=person.id, source="manager", clock_in_at=start, clock_out_at=end, closed_by_id=user.id, created_by_id=user.id)
    db.add(item)
    await db.flush()
    db.add(TeamAttendanceCorrection(clinic_id=user.clinic_id, attendance_id=item.id, revision=1, clock_in_at=start, clock_out_at=end, reason=payload.reason, created_by_id=user.id))
    audit(db, user.clinic_id, user.id, "team_attendance", item.id, "manual_created")
    await db.commit()
    return (await attendance_rows(db, [item]))[0]


# ---------------------------------------------------------------------------------------------
# Who works where and when
# ---------------------------------------------------------------------------------------------


@router.get("/schedule")
async def schedule(
    day: date | None = None, week_start: date | None = None,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
) -> dict:
    if (day is None) == (week_start is None):
        raise HTTPException(422, "Alege fie o zi, fie o săptămână")
    clinic, zone = await clinic_timezone(db, user.clinic_id)
    first = day or (week_start - timedelta(days=week_start.weekday()))
    count = 1 if day else 7
    days = []
    for offset in range(count):
        current = first + timedelta(days=offset)
        start, end = local_bounds(current, clinic.timezone)
        days.append({"date": current.isoformat(), "start": iso(start), "end": iso(end), "hours": int((end - start).total_seconds() // 3600)})
    range_start = local_bounds(first, clinic.timezone)[0]
    range_end = local_bounds(first + timedelta(days=count - 1), clinic.timezone)[1]
    shifts = list(await db.scalars(select(TeamShift).where(
        TeamShift.clinic_id == user.clinic_id, TeamShift.status == "active", TeamShift.starts_at < range_end, TeamShift.ends_at > range_start,
    ).order_by(TeamShift.starts_at, TeamShift.id)))
    leaves = list(await db.scalars(select(TeamLeaveRequest).where(
        TeamLeaveRequest.clinic_id == user.clinic_id, TeamLeaveRequest.status == "approved",
        TeamLeaveRequest.starts_on <= first + timedelta(days=count - 1), TeamLeaveRequest.ends_on >= first,
    ).order_by(TeamLeaveRequest.starts_on, TeamLeaveRequest.id)))
    people = await names(db, {s.user_id for s in shifts} | {l.user_id for l in leaves})
    rooms = await room_names(db, user.clinic_id)
    return {
        "timezone": clinic.timezone, "range_start": iso(range_start), "range_end": iso(range_end), "days": days,
        "shifts": [shift_body(item, people, rooms, zone) for item in shifts],
        # Colleagues see only that the person is away; type and reason stay private.
        "leaves": [{"id": str(l.id), "user_id": str(l.user_id), "user_name": people.get(l.user_id, "—"), "starts_on": l.starts_on.isoformat(), "ends_on": l.ends_on.isoformat()} for l in leaves],
    }

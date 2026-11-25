"""T20 internal messages, tasks and shift handovers.

Access rules enforced here:
- Only members read or write a conversation. Anyone else gets the same 404 as for a missing one.
- In a room conversation a person reads only the messages they sent or were saved as recipient of.
- Room recipients come from the T19 shifts active at the moment of sending and are stored on the
  message, so a later shift change never rewrites who received it.
- Messages, task events, handovers and their acknowledgements are never edited or deleted.
- Nothing here links to patients, and message text is never logged or written to the audit trail.

Lock order: user rows, then conversation, task or shift row. User rows are locked FOR SHARE: that
still blocks a deactivation (FOR UPDATE), but not the key-share locks taken by foreign keys, so two
people writing to each other cannot deadlock.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointments.service import clinic_timezone, local_to_utc, utc
from app.auth.service import get_current_user
from app.collab.models import (
    CollabConversation,
    CollabHandover,
    CollabHandoverAck,
    CollabHandoverTask,
    CollabMember,
    CollabMessage,
    CollabMessageRecipient,
    CollabTask,
    CollabTaskEvent,
)
from app.database import AuditEvent, Room, User, get_db, utcnow
from app.team.models import TeamLeaveRequest, TeamShift

router = APIRouter(prefix="/collab", tags=["collab"])
MANAGERS = ("clinic_manager", "administrator")
MAX_MESSAGE = 4000
PREVIEW = 120
UNRESOLVED = ("open", "in_progress", "blocked")
TERMINAL = ("resolved", "cancelled")
TaskStatus = Literal["open", "in_progress", "blocked", "resolved", "cancelled"]
NO_CONVERSATION = "Conversația nu a fost găsită"
NO_TASK = "Sarcina nu a fost găsită"
NO_HANDOVER = "Predarea nu a fost găsită"


def is_manager(user: User) -> bool:
    return user.role in MANAGERS


def iso(value: datetime | None) -> str | None:
    return None if value is None else utc(value).isoformat().replace("+00:00", "Z")


def plain_text(value: str, limit: int, label: str) -> str:
    """Plain text only: trimmed, not blank, no control characters and encodable for the database."""
    value = value.strip()
    if not value:
        raise ValueError(f"{label} nu poate fi gol")
    if len(value) > limit:
        raise ValueError(f"{label} poate avea cel mult {limit} de caractere")
    if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in value):
        raise ValueError(f"{label} conține caractere nepermise")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} conține caractere nepermise") from exc
    return value


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str
    client_request_id: UUID

    @field_validator("body")
    @classmethod
    def valid_body(cls, value: str) -> str:
        return plain_text(value, MAX_MESSAGE, "Mesajul")


class DirectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["group", "channel"]
    title: str
    member_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return plain_text(value, 120, "Numele")


class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


class ReadMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int = Field(ge=0)


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    description: str | None = None
    assignee_id: UUID
    local_due: datetime | None = None
    due_fold: int | None = Field(default=None, ge=0, le=1)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return plain_text(value, 200, "Titlul")

    @field_validator("description")
    @classmethod
    def valid_description(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return plain_text(value, MAX_MESSAGE, "Descrierea")

    @model_validator(mode="after")
    def naive_due(self) -> "TaskCreate":
        if self.local_due is not None and self.local_due.tzinfo is not None:
            raise ValueError("Termenul se introduce în fusul clinicii, fără offset")
        return self


class TaskChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    status: TaskStatus | None = None
    assignee_id: UUID | None = None
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return plain_text(value, 500, "Motivul")

    @model_validator(mode="after")
    def changes_something(self) -> "TaskChange":
        if self.status is None and self.assignee_id is None:
            raise ValueError("Alege un status sau un responsabil")
        return self


class TaskTake(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class HandoverCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shift_id: UUID
    to_user_id: UUID
    notes: str

    @field_validator("notes")
    @classmethod
    def valid_notes(cls, value: str) -> str:
        return plain_text(value, MAX_MESSAGE, "Notele")


def audit(db: AsyncSession, clinic_id: UUID, actor: UUID, kind: str, entity: UUID, action: str) -> None:
    db.add(AuditEvent(clinic_id=clinic_id, actor_id=actor, entity_type=kind, entity_id=entity, action=action))


async def names(db: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str]:
    wanted = {value for value in ids if value}
    if not wanted:
        return {}
    return {row[0]: row[1] for row in (await db.execute(select(User.id, User.full_name).where(User.id.in_(wanted)))).all()}


async def active_person(db: AsyncSession, clinic_id: UUID, user_id: UUID, *, lock: bool = True) -> User:
    """A person who may be added or assigned. Another clinic looks the same as a missing person."""
    query = select(User).where(User.id == user_id, User.clinic_id == clinic_id)
    if lock:
        query = query.with_for_update(read=True).execution_options(populate_existing=True)
    person = await db.scalar(query)
    if not person:
        raise HTTPException(404, "Persoana nu a fost găsită")
    if not person.is_active:
        raise HTTPException(422, "Persoana este inactivă")
    return person


async def active_sender(db: AsyncSession, clinic_id: UUID, user_id: UUID) -> None:
    """Re-read under lock, so a deactivation committed meanwhile stops the write."""
    person = await db.scalar(select(User).where(User.id == user_id, User.clinic_id == clinic_id).with_for_update(read=True).execution_options(populate_existing=True))
    if not person or not person.is_active:
        raise HTTPException(403, "Contul este inactiv")


# ---------------------------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------------------------


def visible_message(user_id: UUID):
    """Room messages are visible only to their sender and saved recipients."""
    recipient = exists().where(CollabMessageRecipient.message_id == CollabMessage.id, CollabMessageRecipient.user_id == user_id)
    return or_(CollabConversation.kind != "room", CollabMessage.sender_id == user_id, recipient)


async def my_conversation(db: AsyncSession, clinic_id: UUID, user_id: UUID, conversation_id: UUID) -> CollabConversation:
    item = await db.scalar(select(CollabConversation).join(CollabMember, CollabMember.conversation_id == CollabConversation.id).where(
        CollabConversation.id == conversation_id, CollabConversation.clinic_id == clinic_id, CollabMember.user_id == user_id))
    if not item:
        raise HTTPException(404, NO_CONVERSATION)
    return item


async def unread_counts(db: AsyncSession, clinic_id: UUID, user_id: UUID) -> dict[UUID, int]:
    query = (
        select(CollabMessage.conversation_id, func.count())
        .join(CollabConversation, CollabConversation.id == CollabMessage.conversation_id)
        .join(CollabMember, and_(CollabMember.conversation_id == CollabMessage.conversation_id, CollabMember.user_id == user_id))
        .where(CollabMessage.clinic_id == clinic_id, CollabMessage.sender_id != user_id, CollabMessage.seq > CollabMember.last_read_seq, visible_message(user_id))
        .group_by(CollabMessage.conversation_id)
    )
    return {row[0]: row[1] for row in (await db.execute(query)).all()}


async def conversation_rows(db: AsyncSession, clinic_id: UUID, user_id: UUID, only: UUID | None = None) -> list[dict]:
    query = select(CollabConversation, CollabMember.last_read_seq).join(CollabMember, CollabMember.conversation_id == CollabConversation.id).where(
        CollabConversation.clinic_id == clinic_id, CollabMember.user_id == user_id)
    if only:
        query = query.where(CollabConversation.id == only)
    rows = (await db.execute(query)).all()
    if not rows:
        return []
    ids = [row[0].id for row in rows]
    latest = select(CollabMessage.conversation_id.label("cid"), func.max(CollabMessage.seq).label("seq")).join(
        CollabConversation, CollabConversation.id == CollabMessage.conversation_id).where(CollabMessage.conversation_id.in_(ids), visible_message(user_id)).group_by(CollabMessage.conversation_id).subquery()
    last = {item.conversation_id: item for item in await db.scalars(select(CollabMessage).join(latest, and_(CollabMessage.conversation_id == latest.c.cid, CollabMessage.seq == latest.c.seq)))}
    unread = await unread_counts(db, clinic_id, user_id)
    people: dict[UUID, list[tuple[UUID, str, bool]]] = {}
    plain_ids = [row[0].id for row in rows if row[0].kind != "room"]
    if plain_ids:  # a room has no fixed member list: each message carries its own recipients
        for cid, person, name, active in (await db.execute(select(CollabMember.conversation_id, User.id, User.full_name, User.is_active).join(User, User.id == CollabMember.user_id).where(CollabMember.conversation_id.in_(plain_ids)).order_by(User.full_name))).all():
            people.setdefault(cid, []).append((person, name, active))
    rooms = {row[0]: row[1] for row in (await db.execute(select(Room.id, Room.name).where(Room.clinic_id == clinic_id))).all()}
    senders = await names(db, {item.sender_id for item in last.values()})
    result = []
    for item, last_read in rows:
        members = people.get(item.id, [])
        peer = next((row for row in members if row[0] != user_id), None) if item.kind == "direct" else None
        title = peer[1] if peer else rooms.get(item.room_id, "Cabinet") if item.kind == "room" else item.title
        message = last.get(item.id)
        result.append({
            "id": str(item.id), "kind": item.kind, "title": title, "room_id": str(item.room_id) if item.room_id else None,
            "created_by_id": str(item.created_by_id), "last_read_seq": last_read, "unread": unread.get(item.id, 0),
            "can_write": peer[2] if peer else True,
            "members": [{"id": str(row[0]), "full_name": row[1], "is_active": row[2]} for row in members],
            "last_message": {"seq": message.seq, "sender": senders.get(message.sender_id, "—"), "preview": message.body[:PREVIEW], "created_at": iso(message.created_at)} if message else None,
            "activity_at": iso(message.created_at if message else item.created_at),
        })
    return sorted(result, key=lambda row: datetime.fromisoformat(row["activity_at"]), reverse=True)


@router.get("/summary")
async def summary(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    unread = await unread_counts(db, user.clinic_id, user.id)
    pending = await db.scalar(select(func.count()).select_from(CollabHandover).where(
        CollabHandover.clinic_id == user.clinic_id, CollabHandover.to_user_id == user.id,
        ~exists().where(CollabHandoverAck.handover_id == CollabHandover.id)))
    return {"unread": sum(unread.values()), "pending_handovers": pending or 0}


@router.get("/conversations")
async def list_conversations(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await conversation_rows(db, user.clinic_id, user.id)


@router.post("/direct", status_code=status.HTTP_201_CREATED)
async def open_direct(payload: DirectCreate, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    clinic_id, user_id = user.clinic_id, user.id
    if payload.user_id == user_id:
        raise HTTPException(422, "Alege altă persoană")
    key = ":".join(sorted((str(user_id), str(payload.user_id))))
    find = select(CollabConversation.id).where(CollabConversation.clinic_id == clinic_id, CollabConversation.direct_key == key)
    existing = await db.scalar(find)
    if existing:  # the history stays reachable even if the other person became inactive
        response.status_code = status.HTTP_200_OK
        return (await conversation_rows(db, clinic_id, user_id, existing))[0]
    await active_person(db, clinic_id, payload.user_id, lock=False)
    item = CollabConversation(clinic_id=clinic_id, kind="direct", direct_key=key, last_seq=0, created_by_id=user_id)
    db.add(item)
    try:
        await db.flush()
        db.add_all([CollabMember(conversation_id=item.id, user_id=person, clinic_id=clinic_id, last_read_seq=0) for person in (user_id, payload.user_id)])
        audit(db, clinic_id, user_id, "collab_conversation", item.id, "created")
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(find)
        if not existing:
            raise HTTPException(409, "Conversația nu a putut fi creată; reîncearcă") from None
        response.status_code = status.HTTP_200_OK
        return (await conversation_rows(db, clinic_id, user_id, existing))[0]
    return (await conversation_rows(db, clinic_id, user_id, item.id))[0]


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(payload: ConversationCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    clinic_id, user_id = user.clinic_id, user.id
    if payload.kind == "channel" and not is_manager(user):
        raise HTTPException(403, "Numai managerul creează canale")
    others = sorted({person for person in payload.member_ids if person != user_id}, key=str)
    if payload.kind == "group" and not others:
        raise HTTPException(422, "Un grup are nevoie de cel puțin o altă persoană")
    for person in others:
        await active_person(db, clinic_id, person)
    item = CollabConversation(clinic_id=clinic_id, kind=payload.kind, title=payload.title, last_seq=0, created_by_id=user_id)
    db.add(item)
    await db.flush()
    db.add_all([CollabMember(conversation_id=item.id, user_id=person, clinic_id=clinic_id, last_read_seq=0) for person in [user_id, *others]])
    audit(db, clinic_id, user_id, "collab_conversation", item.id, "created")
    await db.commit()
    return (await conversation_rows(db, clinic_id, user_id, item.id))[0]


@router.post("/conversations/{conversation_id}/members", status_code=status.HTTP_201_CREATED)
async def add_member(conversation_id: UUID, payload: MemberAdd, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    clinic_id, user_id, manager = user.clinic_id, user.id, is_manager(user)
    item = await my_conversation(db, clinic_id, user_id, conversation_id)
    if item.kind not in ("group", "channel"):
        raise HTTPException(409, "În această conversație nu se adaugă membri")
    if not manager and (item.kind == "channel" or item.created_by_id != user_id):
        raise HTTPException(403, "Numai managerul sau cel care a creat grupul adaugă membri")
    await active_person(db, clinic_id, payload.user_id)
    locked = await db.scalar(select(CollabConversation).where(CollabConversation.id == conversation_id).with_for_update().execution_options(populate_existing=True))
    if await db.get(CollabMember, (conversation_id, payload.user_id)):
        response.status_code = status.HTTP_200_OK
    else:  # the history is readable, but it does not arrive as unread
        db.add(CollabMember(conversation_id=conversation_id, user_id=payload.user_id, clinic_id=clinic_id, last_read_seq=locked.last_seq))
        audit(db, clinic_id, user_id, "collab_conversation", conversation_id, "member_added")
        await db.commit()
    return (await conversation_rows(db, clinic_id, user_id, conversation_id))[0]


@router.post("/conversations/{conversation_id}/read")
async def mark_read(conversation_id: UUID, payload: ReadMark, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    clinic_id, user_id = user.clinic_id, user.id
    item = await my_conversation(db, clinic_id, user_id, conversation_id)
    target = min(payload.seq, item.last_seq)
    # A single conditional update: a late or repeated call can never move the marker back.
    await db.execute(update(CollabMember).where(CollabMember.conversation_id == conversation_id, CollabMember.user_id == user_id, CollabMember.last_read_seq < target).values(last_read_seq=target))
    await db.commit()
    marker = await db.scalar(select(CollabMember.last_read_seq).where(CollabMember.conversation_id == conversation_id, CollabMember.user_id == user_id).execution_options(populate_existing=True))
    return {"last_read_seq": marker}


# ---------------------------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------------------------


async def message_rows(db: AsyncSession, messages: list[CollabMessage]) -> list[dict]:
    ids = [item.id for item in messages]
    recipients: dict[UUID, list[UUID]] = {}
    if ids:
        for message_id, person in (await db.execute(select(CollabMessageRecipient.message_id, CollabMessageRecipient.user_id).where(CollabMessageRecipient.message_id.in_(ids)))).all():
            recipients.setdefault(message_id, []).append(person)
    people = await names(db, {item.sender_id for item in messages} | {person for rows in recipients.values() for person in rows})
    return [{
        "id": str(item.id), "conversation_id": str(item.conversation_id), "seq": item.seq, "sender_id": str(item.sender_id),
        "sender": people.get(item.sender_id, "—"), "body": item.body, "client_request_id": str(item.client_request_id), "created_at": iso(item.created_at),
        "recipients": sorted(people.get(person, "—") for person in recipients.get(item.id, [])),
    } for item in messages]


async def replay(db: AsyncSession, clinic_id: UUID, sender_id: UUID, payload: MessageCreate, conversation_id: UUID | None, room_id: UUID | None) -> CollabMessage | None:
    existing = await db.scalar(select(CollabMessage).where(CollabMessage.clinic_id == clinic_id, CollabMessage.sender_id == sender_id, CollabMessage.client_request_id == payload.client_request_id))
    if not existing:
        return None
    target = existing.conversation_id == conversation_id
    if room_id is not None:
        target = await db.scalar(select(CollabConversation.room_id).where(CollabConversation.id == existing.conversation_id)) == room_id
    if not target or existing.body != payload.body:
        raise HTTPException(409, "Acest identificator de trimitere a fost folosit pentru alt mesaj")
    return existing


async def room_recipients(db: AsyncSession, clinic_id: UUID, room_id: UUID) -> set[UUID]:
    """People on an active shift in the room right now, without inactive people or approved leave."""
    _, zone = await clinic_timezone(db, clinic_id)
    now = utcnow()
    today = now.astimezone(zone).date()
    on_shift = set(await db.scalars(select(TeamShift.user_id).join(User, User.id == TeamShift.user_id).where(
        TeamShift.clinic_id == clinic_id, TeamShift.room_id == room_id, TeamShift.status == "active",
        TeamShift.starts_at <= now, TeamShift.ends_at > now, User.is_active.is_(True))))
    if not on_shift:
        return set()
    on_leave = set(await db.scalars(select(TeamLeaveRequest.user_id).where(
        TeamLeaveRequest.clinic_id == clinic_id, TeamLeaveRequest.status == "approved", TeamLeaveRequest.user_id.in_(on_shift),
        TeamLeaveRequest.starts_on <= today, TeamLeaveRequest.ends_on >= today)))
    return on_shift - on_leave


async def room_conversation(db: AsyncSession, clinic_id: UUID, room_id: UUID, sender_id: UUID) -> UUID:
    find = select(CollabConversation.id).where(CollabConversation.clinic_id == clinic_id, CollabConversation.room_id == room_id)
    found = await db.scalar(find)
    if found:
        return found
    item = CollabConversation(clinic_id=clinic_id, kind="room", room_id=room_id, last_seq=0, created_by_id=sender_id)
    try:
        async with db.begin_nested():
            db.add(item)
            await db.flush()
        return item.id
    except IntegrityError:  # another sender created it first
        return await db.scalar(find)


async def deliver(db: AsyncSession, response: Response, clinic_id: UUID, sender_id: UUID, payload: MessageCreate, *, conversation_id: UUID | None = None, room_id: UUID | None = None) -> dict:
    await active_sender(db, clinic_id, sender_id)
    previous = await replay(db, clinic_id, sender_id, payload, conversation_id, room_id)
    if previous:
        response.status_code = status.HTTP_200_OK
        return (await message_rows(db, [previous]))[0]
    recipients: set[UUID] = set()
    if room_id is not None:
        room = await db.scalar(select(Room).where(Room.id == room_id, Room.clinic_id == clinic_id))
        if not room:
            raise HTTPException(404, "Cabinetul nu a fost găsit")
        if not room.is_active:
            raise HTTPException(422, "Cabinetul nu este activ")
        room_name = room.name
        on_shift = await room_recipients(db, clinic_id, room_id)
        recipients = on_shift - {sender_id}
        if not recipients:
            detail = f"Ești singura persoană repartizată acum la {room_name}" if on_shift else f"Nimeni nu este repartizat acum la {room_name}"
            raise HTTPException(409, f"{detail}. Mesajul nu a fost trimis.")
        conversation_id = await room_conversation(db, clinic_id, room_id, sender_id)
    conversation = await db.scalar(select(CollabConversation).where(CollabConversation.id == conversation_id).with_for_update().execution_options(populate_existing=True))
    # The shared user lock no longer serializes one sender: a retry may have committed while this one waited.
    committed = await replay(db, clinic_id, sender_id, payload, conversation_id if room_id is None else None, room_id)
    if committed:
        response.status_code = status.HTTP_200_OK
        return (await message_rows(db, [committed]))[0]
    if conversation.kind == "direct":
        peer = await db.scalar(select(User.is_active).join(CollabMember, CollabMember.user_id == User.id).where(CollabMember.conversation_id == conversation.id, User.id != sender_id))
        if not peer:
            raise HTTPException(409, "Persoana este inactivă și nu mai poate primi mesaje. Istoricul rămâne.")
    conversation.last_seq += 1
    message = CollabMessage(clinic_id=clinic_id, conversation_id=conversation.id, seq=conversation.last_seq, sender_id=sender_id, body=payload.body, client_request_id=payload.client_request_id)
    db.add(message)
    try:
        await db.flush()
        if room_id is not None:
            present = set(await db.scalars(select(CollabMember.user_id).where(CollabMember.conversation_id == conversation.id)))
            db.add_all([CollabMember(conversation_id=conversation.id, user_id=person, clinic_id=clinic_id, last_read_seq=0) for person in (recipients | {sender_id}) - present])
            db.add_all([CollabMessageRecipient(message_id=message.id, user_id=person, clinic_id=clinic_id) for person in recipients])
        await db.commit()
    except IntegrityError:
        await db.rollback()
        again = await replay(db, clinic_id, sender_id, payload, conversation_id if room_id is None else None, room_id)
        if not again:
            raise HTTPException(409, "Mesajul nu a putut fi trimis; reîncearcă") from None
        response.status_code = status.HTTP_200_OK
        return (await message_rows(db, [again]))[0]
    return (await message_rows(db, [message]))[0]


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: UUID, limit: int = Query(default=50, ge=1, le=500), before_seq: int | None = Query(default=None, ge=1),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
) -> dict:
    await my_conversation(db, user.clinic_id, user.id, conversation_id)
    visible = select(CollabMessage).join(CollabConversation, CollabConversation.id == CollabMessage.conversation_id).where(
        CollabMessage.conversation_id == conversation_id, visible_message(user.id))
    query = visible if before_seq is None else visible.where(CollabMessage.seq < before_seq)
    rows = list(await db.scalars(query.order_by(CollabMessage.seq.desc()).limit(limit + 1)))
    last_read = await db.scalar(select(CollabMember.last_read_seq).where(CollabMember.conversation_id == conversation_id, CollabMember.user_id == user.id))
    # Room sequences have gaps per person, so the client compares against a real visible message.
    oldest_unread = await db.scalar(select(func.min(CollabMessage.seq)).join(CollabConversation, CollabConversation.id == CollabMessage.conversation_id).where(
        CollabMessage.conversation_id == conversation_id, visible_message(user.id), CollabMessage.sender_id != user.id, CollabMessage.seq > last_read))
    return {"messages": await message_rows(db, list(reversed(rows[:limit]))), "has_more": len(rows) > limit, "oldest_unread_seq": oldest_unread}


@router.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(conversation_id: UUID, payload: MessageCreate, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    clinic_id, user_id = user.clinic_id, user.id
    item = await my_conversation(db, clinic_id, user_id, conversation_id)
    if item.kind == "room":  # a reply goes to whoever is assigned to the room now
        return await deliver(db, response, clinic_id, user_id, payload, room_id=item.room_id)
    return await deliver(db, response, clinic_id, user_id, payload, conversation_id=conversation_id)


@router.post("/rooms/{room_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_room_message(room_id: UUID, payload: MessageCreate, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    return await deliver(db, response, user.clinic_id, user.id, payload, room_id=room_id)


# ---------------------------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------------------------


def task_scope(user: User):
    """A manager sees every task; anyone else sees what they created or are responsible for."""
    if is_manager(user):
        return CollabTask.clinic_id == user.clinic_id
    return and_(CollabTask.clinic_id == user.clinic_id, or_(CollabTask.assignee_id == user.id, CollabTask.created_by_id == user.id))


def task_read_scope(user: User):
    """The recipient of a handover also reads the tasks frozen in it, without being able to change them."""
    if is_manager(user):
        return CollabTask.clinic_id == user.clinic_id
    received = exists().where(CollabHandoverTask.task_id == CollabTask.id, CollabHandoverTask.handover_id == CollabHandover.id, CollabHandover.to_user_id == user.id)
    return and_(CollabTask.clinic_id == user.clinic_id, or_(CollabTask.assignee_id == user.id, CollabTask.created_by_id == user.id, received))


async def task_rows(db: AsyncSession, tasks: list[CollabTask]) -> list[dict]:
    ids = [item.id for item in tasks]
    events: dict[UUID, list[CollabTaskEvent]] = {}
    if ids:
        for row in await db.scalars(select(CollabTaskEvent).where(CollabTaskEvent.task_id.in_(ids)).order_by(CollabTaskEvent.task_id, CollabTaskEvent.version)):
            events.setdefault(row.task_id, []).append(row)
    wanted: set[UUID | None] = {item.assignee_id for item in tasks} | {item.created_by_id for item in tasks}
    for rows in events.values():
        for row in rows:
            wanted |= {row.actor_id, row.from_assignee_id, row.to_assignee_id}
    people = await names(db, wanted)
    now = utcnow()
    return [{
        "id": str(item.id), "title": item.title, "description": item.description, "assignee_id": str(item.assignee_id), "assignee": people.get(item.assignee_id, "—"),
        "created_by_id": str(item.created_by_id), "created_by": people.get(item.created_by_id, "—"), "due_at": iso(item.due_at), "status": item.status, "version": item.version,
        "overdue": item.due_at is not None and item.status in UNRESOLVED and utc(item.due_at) < now, "created_at": iso(item.created_at), "updated_at": iso(item.updated_at),
        "events": [{
            "version": row.version, "from_status": row.from_status, "to_status": row.to_status, "from_assignee": people.get(row.from_assignee_id) if row.from_assignee_id else None,
            "to_assignee": people.get(row.to_assignee_id, "—"), "reason": row.reason, "actor": people.get(row.actor_id, "—"), "created_at": iso(row.created_at),
        } for row in events.get(item.id, [])],
    } for item in tasks]


@router.get("/tasks")
async def list_tasks(
    mine: bool = False, overdue: bool = False, task_status: TaskStatus | None = Query(default=None, alias="status"),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
) -> list[dict]:
    query = select(CollabTask).where(task_read_scope(user))
    if mine:
        query = query.where(CollabTask.assignee_id == user.id)
    if overdue:
        query = query.where(CollabTask.due_at.is_not(None), CollabTask.due_at < utcnow(), CollabTask.status.in_(UNRESOLVED))
    if task_status:
        query = query.where(CollabTask.status == task_status)
    return await task_rows(db, list(await db.scalars(query.order_by(CollabTask.created_at.desc(), CollabTask.id).limit(200))))


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    clinic_id, user_id = user.clinic_id, user.id
    due = None
    if payload.local_due is not None:
        clinic, _ = await clinic_timezone(db, clinic_id)
        due = local_to_utc(payload.local_due, clinic.timezone, payload.due_fold)
        if due <= utcnow():
            raise HTTPException(422, "Termenul trebuie să fie în viitor")
    await active_person(db, clinic_id, payload.assignee_id)
    item = CollabTask(clinic_id=clinic_id, title=payload.title, description=payload.description, assignee_id=payload.assignee_id, due_at=due, status="open", version=1, created_by_id=user_id)
    db.add(item)
    await db.flush()
    db.add(CollabTaskEvent(clinic_id=clinic_id, task_id=item.id, version=1, to_status="open", to_assignee_id=payload.assignee_id, actor_id=user_id))
    audit(db, clinic_id, user_id, "collab_task", item.id, "created")
    await db.commit()
    return (await task_rows(db, [item]))[0]


@router.post("/tasks/{task_id}/changes")
async def change_task(task_id: UUID, payload: TaskChange, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    clinic_id, user_id, manager = user.clinic_id, user.id, is_manager(user)
    if not await db.scalar(select(CollabTask.id).where(CollabTask.id == task_id, task_read_scope(user))):
        raise HTTPException(404, NO_TASK)
    if not await db.scalar(select(CollabTask.id).where(CollabTask.id == task_id, task_scope(user))):
        raise HTTPException(403, "Preia sarcina din predare înainte de a lucra pe ea")
    if payload.assignee_id is not None:
        await active_person(db, clinic_id, payload.assignee_id)
    item = await db.scalar(select(CollabTask).where(CollabTask.id == task_id).with_for_update().execution_options(populate_existing=True))
    if item.version != payload.expected_version:
        raise HTTPException(409, "Sarcina a fost modificată între timp. Reîncarcă și încearcă din nou.")
    new_status = payload.status or item.status
    new_assignee = payload.assignee_id or item.assignee_id
    if new_status == item.status and new_assignee == item.assignee_id:
        raise HTTPException(422, "Nu este nicio schimbare")
    if item.status in TERMINAL:
        if new_status != "open" or new_assignee != item.assignee_id:
            raise HTTPException(409, "O sarcină închisă poate fi doar redeschisă")
        if not manager:
            raise HTTPException(403, "Numai managerul redeschide o sarcină închisă")
        if not payload.reason:
            raise HTTPException(422, "Motivul este obligatoriu la redeschidere")
    if new_status != item.status and new_status in ("blocked", "cancelled") and not payload.reason:
        raise HTTPException(422, "Motivul este obligatoriu la blocare și anulare")
    old_status, old_assignee = item.status, item.assignee_id
    item.status, item.assignee_id, item.version, item.updated_at = new_status, new_assignee, item.version + 1, utcnow()
    db.add(CollabTaskEvent(clinic_id=clinic_id, task_id=item.id, version=item.version, from_status=old_status, to_status=new_status,
                           from_assignee_id=old_assignee, to_assignee_id=new_assignee, reason=payload.reason, actor_id=user_id))
    audit(db, clinic_id, user_id, "collab_task", item.id, "changed")
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Sarcina a fost modificată între timp. Reîncarcă și încearcă din nou.") from exc
    return (await task_rows(db, [item]))[0]


# ---------------------------------------------------------------------------------------------
# Shift handovers
# ---------------------------------------------------------------------------------------------


def handover_scope(user: User):
    if is_manager(user):
        return CollabHandover.clinic_id == user.clinic_id
    return and_(CollabHandover.clinic_id == user.clinic_id, or_(
        CollabHandover.from_user_id == user.id, CollabHandover.to_user_id == user.id, CollabHandover.created_by_id == user.id))


async def handover_rows(db: AsyncSession, items: list[CollabHandover], viewer_id: UUID) -> list[dict]:
    ids = [item.id for item in items]
    tasks: dict[UUID, list[CollabHandoverTask]] = {}
    acks: dict[UUID, CollabHandoverAck] = {}
    shifts: dict[UUID, TeamShift] = {}
    live: dict[UUID, CollabTask] = {}
    if ids:
        for row in await db.scalars(select(CollabHandoverTask).where(CollabHandoverTask.handover_id.in_(ids)).order_by(CollabHandoverTask.title)):
            tasks.setdefault(row.handover_id, []).append(row)
        acks = {row.handover_id: row for row in await db.scalars(select(CollabHandoverAck).where(CollabHandoverAck.handover_id.in_(ids)))}
        shifts = {row.id: row for row in await db.scalars(select(TeamShift).where(TeamShift.id.in_([item.shift_id for item in items])))}
        wanted = {row.task_id for rows in tasks.values() for row in rows}
        if wanted:  # current state only decides the action; the frozen fields below never change
            live = {row.id: row for row in await db.scalars(select(CollabTask).where(CollabTask.id.in_(wanted)).execution_options(populate_existing=True))}
    people = await names(db, {item.from_user_id for item in items} | {item.to_user_id for item in items} | {item.created_by_id for item in items})

    def frozen(item: CollabHandover, row: CollabHandoverTask) -> dict:
        current = live.get(row.task_id)
        can_take = bool(current and item.to_user_id == viewer_id and item.id in acks and current.status in UNRESOLVED and current.assignee_id == item.from_user_id)
        return {"task_id": str(row.task_id), "title": row.title, "status": row.status, "due_at": iso(row.due_at), "can_take": can_take,
                "version": current.version if can_take else None, "with_me": bool(current and item.to_user_id == viewer_id and current.assignee_id == viewer_id)}

    return [{
        "id": str(item.id), "shift_id": str(item.shift_id), "shift_starts_at": iso(shifts[item.shift_id].starts_at), "shift_ends_at": iso(shifts[item.shift_id].ends_at),
        "from_user_id": str(item.from_user_id), "from_user": people.get(item.from_user_id, "—"), "to_user_id": str(item.to_user_id), "to_user": people.get(item.to_user_id, "—"),
        "created_by": people.get(item.created_by_id, "—"), "notes": item.notes, "created_at": iso(item.created_at),
        "acknowledged_at": iso(acks[item.id].acknowledged_at) if item.id in acks else None,
        "tasks": [frozen(item, row) for row in tasks.get(item.id, [])],
    } for item in items]


@router.get("/handovers")
async def list_handovers(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await handover_rows(db, list(await db.scalars(select(CollabHandover).where(handover_scope(user)).order_by(CollabHandover.created_at.desc(), CollabHandover.id).limit(100))), user.id)


@router.post("/handovers", status_code=status.HTTP_201_CREATED)
async def create_handover(payload: HandoverCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    clinic_id, user_id, manager = user.clinic_id, user.id, is_manager(user)
    await active_person(db, clinic_id, payload.to_user_id)
    query = select(TeamShift).where(TeamShift.id == payload.shift_id, TeamShift.clinic_id == clinic_id)
    if not manager:  # someone else's shift looks the same as a missing one
        query = query.where(TeamShift.user_id == user_id)
    shift = await db.scalar(query.with_for_update().execution_options(populate_existing=True))
    if not shift:
        raise HTTPException(404, "Tura nu a fost găsită")
    if shift.status != "active":
        raise HTTPException(409, "Tura este anulată")
    if utc(shift.starts_at) > utcnow():
        raise HTTPException(422, "Predarea se face după începerea turei")
    if shift.user_id == payload.to_user_id:
        raise HTTPException(422, "Predarea se face către altă persoană")
    if await db.scalar(select(CollabHandover.id).where(CollabHandover.shift_id == shift.id)):
        raise HTTPException(409, "Această tură are deja o predare. Predarea nu se rescrie.")
    item = CollabHandover(clinic_id=clinic_id, shift_id=shift.id, from_user_id=shift.user_id, to_user_id=payload.to_user_id, notes=payload.notes, created_by_id=user_id)
    db.add(item)
    try:
        await db.flush()
        open_tasks = await db.scalars(select(CollabTask).where(CollabTask.clinic_id == clinic_id, CollabTask.assignee_id == shift.user_id, CollabTask.status.in_(UNRESOLVED)))
        db.add_all([CollabHandoverTask(handover_id=item.id, task_id=task.id, clinic_id=clinic_id, title=task.title, status=task.status, due_at=task.due_at) for task in open_tasks])
        audit(db, clinic_id, user_id, "collab_handover", item.id, "created")
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Această tură are deja o predare. Predarea nu se rescrie.") from exc
    return (await handover_rows(db, [item], user_id))[0]


@router.post("/handovers/{handover_id}/acknowledge")
async def acknowledge_handover(handover_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    clinic_id, user_id = user.clinic_id, user.id
    item = await db.scalar(select(CollabHandover).where(CollabHandover.id == handover_id, handover_scope(user)))
    if not item:
        raise HTTPException(404, NO_HANDOVER)
    if item.to_user_id != user_id:
        raise HTTPException(403, "Numai destinatarul confirmă primirea")
    if not await db.get(CollabHandoverAck, handover_id):
        db.add(CollabHandoverAck(handover_id=handover_id, clinic_id=clinic_id, user_id=user_id))
        audit(db, clinic_id, user_id, "collab_handover", handover_id, "acknowledged")
        try:
            await db.commit()
        except IntegrityError:  # a double click: the first confirmation stands
            await db.rollback()
    item = await db.scalar(select(CollabHandover).where(CollabHandover.id == handover_id))
    return (await handover_rows(db, [item], user_id))[0]


@router.post("/handovers/{handover_id}/tasks/{task_id}/take")
async def take_handover_task(handover_id: UUID, task_id: UUID, payload: TaskTake, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """A change of assignee, recorded like any other. The handover and its frozen list stay as written."""
    clinic_id, user_id = user.clinic_id, user.id
    item = await db.scalar(select(CollabHandover).where(CollabHandover.id == handover_id, handover_scope(user)))
    if not item or not await db.get(CollabHandoverTask, (handover_id, task_id)):
        raise HTTPException(404, NO_TASK)
    if item.to_user_id != user_id:
        raise HTTPException(403, "Numai destinatarul predării preia sarcina")
    if not await db.get(CollabHandoverAck, handover_id):
        raise HTTPException(409, "Confirmă întâi primirea predării")
    from_user_id = item.from_user_id
    await active_sender(db, clinic_id, user_id)
    task = await db.scalar(select(CollabTask).where(CollabTask.id == task_id, CollabTask.clinic_id == clinic_id).with_for_update().execution_options(populate_existing=True))
    if task.version != payload.expected_version:
        raise HTTPException(409, "Sarcina a fost modificată între timp. Reîncarcă și încearcă din nou.")
    if task.status not in UNRESOLVED:
        raise HTTPException(409, "Sarcina este deja închisă")
    if task.assignee_id != from_user_id:
        raise HTTPException(409, "Sarcina nu mai este la persoana care a predat")
    task.assignee_id, task.version, task.updated_at = user_id, task.version + 1, utcnow()
    db.add(CollabTaskEvent(clinic_id=clinic_id, task_id=task.id, version=task.version, from_status=task.status, to_status=task.status,
                           from_assignee_id=from_user_id, to_assignee_id=user_id, reason="Preluată din predarea de tură", actor_id=user_id))
    audit(db, clinic_id, user_id, "collab_task", task.id, "taken")
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Sarcina a fost modificată între timp. Reîncarcă și încearcă din nou.") from exc
    return (await task_rows(db, [task]))[0]

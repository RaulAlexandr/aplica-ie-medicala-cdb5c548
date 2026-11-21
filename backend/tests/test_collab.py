"""T20 rules on SQLite: membership, messages, room delivery, unread, tasks and handovers."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.collab.models import (
    CollabConversation,
    CollabMessage,
    CollabMessageRecipient,
    CollabTask,
    CollabTaskEvent,
)
from app.collab.router import router as collab_router
from app.database import AuditEvent, Base, Clinic, User, get_db
from app.main import app
from app.team.models import TeamLeaveRequest, TeamShift

PASSWORD = "correct horse battery staple"
SECRET_TEXT = "text-fictiv-care-nu-apare-in-audit"


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
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def clinic(client, sessions, timezone="Europe/Bucharest"):
    registered = await client.post("/api/auth/register", json={"clinic_name": "Clinica fictivă T20", "full_name": "Manager Fictiv", "email": f"m-{uuid4().hex[:8]}@example.com", "password": PASSWORD})
    assert registered.status_code == 201, registered.text
    auth = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=auth)).json()
    async with sessions() as session:
        (await session.get(Clinic, UUID(me["clinic_id"]))).timezone = timezone
        await session.commit()
    room = (await client.post("/api/rooms", headers=auth, json={"name": "Cabinet 3"})).json()
    return {"auth": auth, "id": me["id"], "clinic_id": me["clinic_id"], "room": room["id"]}


async def person(sessions, clinic_id, role, name=None):
    user = User(id=uuid4(), clinic_id=UUID(clinic_id), email=f"{role}-{uuid4().hex[:8]}@example.com", password_hash="unused", full_name=name or f"{role} fictiv", role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
    return {"auth": {"Authorization": f"Bearer {create_access_token(user)}"}, "id": str(user.id)}


async def deactivate(sessions, user_id):
    async with sessions() as session:
        (await session.get(User, UUID(user_id))).is_active = False
        await session.commit()


async def on_shift(sessions, c, user_id, post, *, start=-1, end=3, room=True):
    now = datetime.now(UTC)
    item = TeamShift(id=uuid4(), clinic_id=UUID(c["clinic_id"]), user_id=UUID(user_id), room_id=UUID(c["room"]) if room else None, post=post,
                     starts_at=now + timedelta(hours=start), ends_at=now + timedelta(hours=end), status="active", created_by_id=UUID(c["id"]))
    async with sessions() as session:
        session.add(item)
        await session.commit()
    return str(item.id)


def message(body="Mesaj fictiv", key=None):
    return {"body": body, "client_request_id": key or str(uuid4())}


async def direct(client, one, two_id):
    response = await client.post("/api/collab/direct", headers=one["auth"], json={"user_id": two_id})
    assert response.status_code in (200, 201), response.text
    return response.json()


async def send(client, who, conversation_id, body="Mesaj fictiv", key=None):
    return await client.post(f"/api/collab/conversations/{conversation_id}/messages", headers=who["auth"], json=message(body, key))


async def unread(client, who):
    return (await client.get("/api/collab/summary", headers=who["auth"])).json()["unread"]


async def new_task(client, who, assignee, **extra):
    response = await client.post("/api/collab/tasks", headers=who["auth"], json={"title": "Sarcină fictivă", "assignee_id": assignee, **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def change(client, who, task, **body):
    return await client.post(f"/api/collab/tasks/{task['id']}/changes", headers=who["auth"], json={"expected_version": task["version"], **body})


# ---------------------------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_conversation_is_unique_per_pair_in_both_directions(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant = await person(sessions, c["clinic_id"], "doctor", "Medic Fictiv"), await person(sessions, c["clinic_id"], "assistant", "Asistent Fictiv")
    first = await client.post("/api/collab/direct", headers=doctor["auth"], json={"user_id": assistant["id"]})
    second = await client.post("/api/collab/direct", headers=assistant["auth"], json={"user_id": doctor["id"]})
    assert (first.status_code, second.status_code) == (201, 200) and first.json()["id"] == second.json()["id"]
    assert first.json()["title"] == "Asistent Fictiv" and second.json()["title"] == "Medic Fictiv"
    assert (await client.post("/api/collab/direct", headers=doctor["auth"], json={"user_id": doctor["id"]})).status_code == 422
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CollabConversation)) == 1


@pytest.mark.asyncio
async def test_non_member_and_other_clinic_get_the_same_404_as_a_missing_conversation(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    doctor, assistant, outsider = [await person(sessions, c["clinic_id"], role) for role in ("doctor", "assistant", "reception")]
    item = await direct(client, doctor, assistant["id"])
    assert (await send(client, doctor, item["id"], SECRET_TEXT)).status_code == 201
    missing = str(uuid4())
    for who in (outsider, c, other):  # the manager is not a member either
        for path in ("messages",):
            real = await client.get(f"/api/collab/conversations/{item['id']}/{path}", headers=who["auth"])
            fake = await client.get(f"/api/collab/conversations/{missing}/{path}", headers=who["auth"])
            assert real.status_code == fake.status_code == 404 and real.json() == fake.json()
        assert (await send(client, who, item["id"])).status_code == 404
        assert (await client.post(f"/api/collab/conversations/{item['id']}/read", headers=who["auth"], json={"seq": 1})).status_code == 404
        assert (await client.post(f"/api/collab/conversations/{item['id']}/members", headers=who["auth"], json={"user_id": who["id"]})).status_code == 404
        listed = await client.get("/api/collab/conversations", headers=who["auth"])
        assert SECRET_TEXT not in listed.text and listed.json() == []
    foreign = await client.post("/api/collab/direct", headers=other["auth"], json={"user_id": doctor["id"]})
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_group_and_channel_roles_and_members(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant, reception = [await person(sessions, c["clinic_id"], role) for role in ("doctor", "assistant", "reception")]
    assert (await client.post("/api/collab/conversations", headers=doctor["auth"], json={"kind": "channel", "title": "Anunțuri"})).status_code == 403
    assert (await client.post("/api/collab/conversations", headers=doctor["auth"], json={"kind": "group", "title": "Singur"})).status_code == 422
    assert (await client.post("/api/collab/conversations", headers=doctor["auth"], json={"kind": "group", "title": "   ", "member_ids": [assistant["id"]]})).status_code == 422
    group = await client.post("/api/collab/conversations", headers=doctor["auth"], json={"kind": "group", "title": "Tură dimineață", "member_ids": [assistant["id"]]})
    assert group.status_code == 201 and len(group.json()["members"]) == 2
    assert (await send(client, doctor, group.json()["id"], "Înainte de intrare")).status_code == 201
    url = f"/api/collab/conversations/{group.json()['id']}/members"
    assert (await client.post(url, headers=assistant["auth"], json={"user_id": reception["id"]})).status_code == 403
    added = await client.post(url, headers=doctor["auth"], json={"user_id": reception["id"]})
    assert added.status_code == 201
    assert (await client.post(url, headers=doctor["auth"], json={"user_id": reception["id"]})).status_code == 200
    assert await unread(client, reception) == 0  # earlier history does not arrive as unread
    assert len((await client.get(f"/api/collab/conversations/{group.json()['id']}/messages", headers=reception["auth"])).json()["messages"]) == 1
    channel = await client.post("/api/collab/conversations", headers=c["auth"], json={"kind": "channel", "title": "Anunțuri", "member_ids": [doctor["id"]]})
    assert channel.status_code == 201 and channel.json()["kind"] == "channel"
    assert (await client.post(f"/api/collab/conversations/{channel.json()['id']}/members", headers=doctor["auth"], json={"user_id": assistant["id"]})).status_code == 403
    assert (await client.post(f"/api/collab/conversations/{channel.json()['id']}/members", headers=c["auth"], json={"user_id": assistant["id"]})).status_code == 201


@pytest.mark.asyncio
async def test_inactive_person_cannot_be_added_or_write_and_history_remains(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    doctor, gone, stranger = await person(sessions, c["clinic_id"], "doctor"), await person(sessions, c["clinic_id"], "assistant"), await person(sessions, other["clinic_id"], "doctor")
    item = await direct(client, doctor, gone["id"])
    assert (await send(client, gone, item["id"], "Scris înainte de plecare")).status_code == 201
    await deactivate(sessions, gone["id"])
    assert (await send(client, gone, item["id"], "După plecare")).status_code == 401
    refused = await send(client, doctor, item["id"], "Către persoana plecată")
    assert refused.status_code == 409 and "inactivă" in refused.json()["detail"]
    history = await client.get(f"/api/collab/conversations/{item['id']}/messages", headers=doctor["auth"])
    assert [row["body"] for row in history.json()["messages"]] == ["Scris înainte de plecare"]
    reopened = await direct(client, doctor, gone["id"])
    assert reopened["id"] == item["id"] and reopened["can_write"] is False
    fresh = await person(sessions, c["clinic_id"], "reception")
    await deactivate(sessions, fresh["id"])
    assert (await client.post("/api/collab/direct", headers=doctor["auth"], json={"user_id": fresh["id"]})).status_code == 422
    assert (await client.post("/api/collab/conversations", headers=doctor["auth"], json={"kind": "group", "title": "G", "member_ids": [fresh["id"]]})).status_code == 422
    assert (await client.post("/api/collab/conversations", headers=doctor["auth"], json={"kind": "group", "title": "G", "member_ids": [stranger["id"]]})).status_code == 404


# ---------------------------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_text_validation(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant = await person(sessions, c["clinic_id"], "doctor"), await person(sessions, c["clinic_id"], "assistant")
    item = await direct(client, doctor, assistant["id"])
    for bad in ("", "   \n\t ", "x" * 4001, "cu\x00nul", "cu\x07clopot"):
        assert (await send(client, doctor, item["id"], bad)).status_code == 422
    assert (await send(client, doctor, item["id"], "y" * 4000)).status_code == 201
    trimmed = await send(client, doctor, item["id"], "  două\nrânduri  ")
    assert trimmed.json()["body"] == "două\nrânduri"
    markup = await send(client, doctor, item["id"], "<b>text simplu</b>")
    assert markup.json()["body"] == "<b>text simplu</b>"
    assert (await client.post(f"/api/collab/conversations/{item['id']}/messages", headers=doctor["auth"], json={"body": "x"})).status_code == 422


def test_no_route_edits_or_deletes_messages_tasks_events_or_handovers():
    edits = {(method, route.path) for route in collab_router.routes for method in route.methods if method in {"PUT", "PATCH", "DELETE"}}
    assert edits == set()


@pytest.mark.asyncio
async def test_client_request_id_replays_same_content_and_refuses_other_content(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant, reception = [await person(sessions, c["clinic_id"], role) for role in ("doctor", "assistant", "reception")]
    item, second = await direct(client, doctor, assistant["id"]), await direct(client, doctor, reception["id"])
    key = str(uuid4())
    first = await send(client, doctor, item["id"], "O singură dată", key)
    again = await send(client, doctor, item["id"], "  O singură dată ", key)
    assert (first.status_code, again.status_code) == (201, 200) and first.json()["id"] == again.json()["id"]
    assert (await send(client, doctor, item["id"], "Alt text", key)).status_code == 409
    assert (await send(client, doctor, second["id"], "O singură dată", key)).status_code == 409
    other_sender = await send(client, assistant, item["id"], "Alt expeditor", key)
    assert other_sender.status_code == 201  # the id belongs to one sender
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CollabMessage)) == 2


@pytest.mark.asyncio
async def test_message_text_is_not_written_to_the_audit_trail_or_logs(env, caplog):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant = await person(sessions, c["clinic_id"], "doctor"), await person(sessions, c["clinic_id"], "assistant")
    item = await direct(client, doctor, assistant["id"])
    await send(client, doctor, item["id"], SECRET_TEXT)
    assert SECRET_TEXT not in caplog.text
    async with sessions() as session:
        rows = list(await session.scalars(select(AuditEvent).where(AuditEvent.entity_type.like("collab%"))))
    assert all(SECRET_TEXT not in f"{row.entity_type}{row.action}" for row in rows)


def test_collab_tables_do_not_reference_patients():
    tables = [table for name, table in Base.metadata.tables.items() if name.startswith("collab_")]
    assert len(tables) == 9
    referenced = {fk.column.table.name for table in tables for fk in table.foreign_keys}
    assert not {name for name in referenced if "patient" in name or name in {"appointments", "documents"}}
    assert not any("patient" in column.name for table in tables for column in table.columns)


# ---------------------------------------------------------------------------------------------
# Room delivery
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_room_message_reaches_the_people_assigned_right_now(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant = await person(sessions, c["clinic_id"], "doctor", "Medic Cabinet"), await person(sessions, c["clinic_id"], "assistant", "Asistent Cabinet")
    reception = await person(sessions, c["clinic_id"], "reception")
    later = await person(sessions, c["clinic_id"], "doctor", "Medic Mai Târziu")
    await on_shift(sessions, c, doctor["id"], "doctor")
    await on_shift(sessions, c, assistant["id"], "assistant")
    await on_shift(sessions, c, later["id"], "doctor", start=4, end=8)
    sent = await client.post(f"/api/collab/rooms/{c['room']}/messages", headers=reception["auth"], json=message("Pacientul fictiv a sosit"))
    assert sent.status_code == 201, sent.text
    assert sent.json()["recipients"] == ["Asistent Cabinet", "Medic Cabinet"]
    assert await unread(client, doctor) == 1 and await unread(client, assistant) == 1
    assert await unread(client, later) == 0 and await unread(client, reception) == 0
    assert (await client.get("/api/collab/conversations", headers=later["auth"])).json() == []
    seen = (await client.get("/api/collab/conversations", headers=doctor["auth"])).json()
    assert seen[0]["kind"] == "room" and seen[0]["title"] == "Cabinet 3" and seen[0]["members"] == []
    reply = await send(client, doctor, seen[0]["id"], "Vin imediat")
    assert reply.status_code == 201 and reply.json()["recipients"] == ["Asistent Cabinet"]


@pytest.mark.asyncio
async def test_room_recipients_exclude_inactive_leave_cancelled_and_finished_shifts(env):
    client, sessions = env
    c = await clinic(client, sessions)
    sender = await person(sessions, c["clinic_id"], "reception")
    present = await person(sessions, c["clinic_id"], "doctor", "Medic Prezent")
    gone, away, cancelled, finished = [await person(sessions, c["clinic_id"], role) for role in ("assistant", "doctor", "doctor", "assistant")]
    await on_shift(sessions, c, present["id"], "doctor")
    await on_shift(sessions, c, gone["id"], "assistant")
    await on_shift(sessions, c, away["id"], "doctor", start=-2, end=4)
    cancelled_shift = await on_shift(sessions, c, cancelled["id"], "doctor", start=-3, end=2)
    await on_shift(sessions, c, finished["id"], "assistant", start=-6, end=-1)
    await deactivate(sessions, gone["id"])
    today = datetime.now(UTC).astimezone(ZoneInfo("Europe/Bucharest")).date()
    async with sessions() as session:
        session.add(TeamLeaveRequest(clinic_id=UUID(c["clinic_id"]), user_id=UUID(away["id"]), leave_type="annual", starts_on=today, ends_on=today, status="approved", requested_by_id=UUID(away["id"])))
        row = await session.get(TeamShift, UUID(cancelled_shift))
        row.status, row.cancelled_at, row.cancelled_by_id, row.cancel_reason = "cancelled", datetime.now(UTC), UUID(c["id"]), "Anulată"
        await session.commit()
    sent = await client.post(f"/api/collab/rooms/{c['room']}/messages", headers=sender["auth"], json=message())
    assert sent.status_code == 201 and sent.json()["recipients"] == ["Medic Prezent"]


@pytest.mark.asyncio
async def test_room_without_anyone_assigned_is_409_and_nothing_is_stored(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    reception = await person(sessions, c["clinic_id"], "reception")
    empty = await client.post(f"/api/collab/rooms/{c['room']}/messages", headers=reception["auth"], json=message("Nu se pierde"))
    assert empty.status_code == 409 and "Nimeni nu este repartizat acum la Cabinet 3" in empty.json()["detail"] and "nu a fost trimis" in empty.json()["detail"]
    alone = await person(sessions, c["clinic_id"], "doctor")
    await on_shift(sessions, c, alone["id"], "doctor")
    self_only = await client.post(f"/api/collab/rooms/{c['room']}/messages", headers=alone["auth"], json=message())
    assert self_only.status_code == 409 and "singura persoană" in self_only.json()["detail"]
    assert (await client.post(f"/api/collab/rooms/{other['room']}/messages", headers=reception["auth"], json=message())).status_code == 404
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CollabMessage)) == 0
        assert await session.scalar(select(func.count()).select_from(CollabConversation)) == 0


@pytest.mark.asyncio
async def test_saved_recipients_survive_a_shift_change_and_a_new_person_cannot_read_old_messages(env):
    client, sessions = env
    c = await clinic(client, sessions)
    reception, first, second = [await person(sessions, c["clinic_id"], role, name) for role, name in (("reception", "Recepție"), ("doctor", "Primul Medic"), ("doctor", "Al Doilea Medic"))]
    shift_id = await on_shift(sessions, c, first["id"], "doctor")
    key = str(uuid4())
    sent = await client.post(f"/api/collab/rooms/{c['room']}/messages", headers=reception["auth"], json=message("Pentru primul", key))
    assert sent.json()["recipients"] == ["Primul Medic"]
    async with sessions() as session:
        row = await session.get(TeamShift, UUID(shift_id))
        row.status, row.cancelled_at, row.cancelled_by_id, row.cancel_reason = "cancelled", datetime.now(UTC), UUID(c["id"]), "Schimb"
        await session.commit()
    await on_shift(sessions, c, second["id"], "doctor")
    retry = await client.post(f"/api/collab/rooms/{c['room']}/messages", headers=reception["auth"], json=message("Pentru primul", key))
    assert retry.status_code == 200 and retry.json()["recipients"] == ["Primul Medic"]
    newer = await client.post(f"/api/collab/rooms/{c['room']}/messages", headers=reception["auth"], json=message("Pentru al doilea"))
    assert newer.json()["recipients"] == ["Al Doilea Medic"]
    conversation = sent.json()["conversation_id"]
    bodies = lambda response: [row["body"] for row in response.json()["messages"]]
    assert bodies(await client.get(f"/api/collab/conversations/{conversation}/messages", headers=first["auth"])) == ["Pentru primul"]
    assert bodies(await client.get(f"/api/collab/conversations/{conversation}/messages", headers=second["auth"])) == ["Pentru al doilea"]
    assert bodies(await client.get(f"/api/collab/conversations/{conversation}/messages", headers=reception["auth"])) == ["Pentru primul", "Pentru al doilea"]
    assert await unread(client, first) == 1 and await unread(client, second) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CollabMessageRecipient)) == 2


# ---------------------------------------------------------------------------------------------
# Unread
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_marker_is_idempotent_never_moves_back_and_ignores_own_messages(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant = await person(sessions, c["clinic_id"], "doctor"), await person(sessions, c["clinic_id"], "assistant")
    item = await direct(client, doctor, assistant["id"])
    for index in range(3):
        await send(client, doctor, item["id"], f"Mesaj {index}")
    assert await unread(client, doctor) == 0 and await unread(client, assistant) == 3
    url = f"/api/collab/conversations/{item['id']}/read"
    assert (await client.post(url, headers=assistant["auth"], json={"seq": 2})).json() == {"last_read_seq": 2}
    assert (await client.post(url, headers=assistant["auth"], json={"seq": 2})).json() == {"last_read_seq": 2}
    assert (await client.post(url, headers=assistant["auth"], json={"seq": 1})).json() == {"last_read_seq": 2}
    assert await unread(client, assistant) == 1
    assert (await client.post(url, headers=assistant["auth"], json={"seq": 999})).json() == {"last_read_seq": 3}
    assert await unread(client, assistant) == 0
    assert (await client.post(url, headers=assistant["auth"], json={"seq": -1})).status_code == 422
    await send(client, assistant, item["id"], "Răspuns propriu")
    assert await unread(client, assistant) == 0 and await unread(client, doctor) == 1


# ---------------------------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_is_tracked_until_resolved_with_a_new_record_for_each_change(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant = await person(sessions, c["clinic_id"], "doctor", "Medic"), await person(sessions, c["clinic_id"], "assistant", "Asistent")
    task = await new_task(client, doctor, assistant["id"], description="Descriere fictivă")
    assert (task["status"], task["version"], len(task["events"])) == ("open", 1, 1)
    task = (await change(client, assistant, task, status="in_progress")).json()
    assert (await change(client, assistant, task, status="blocked")).status_code == 422
    task = (await change(client, assistant, task, status="blocked", reason="Lipsește materialul")).json()
    task = (await change(client, doctor, task, assignee_id=doctor["id"])).json()
    assert task["assignee"] == "Medic" and task["status"] == "blocked"
    task = (await change(client, doctor, task, status="resolved")).json()
    assert task["status"] == "resolved" and task["version"] == 5
    assert [(e["from_status"], e["to_status"]) for e in task["events"]] == [(None, "open"), ("open", "in_progress"), ("in_progress", "blocked"), ("blocked", "blocked"), ("blocked", "resolved")]
    assert task["events"][2]["reason"] == "Lipsește materialul" and task["events"][2]["actor"] == "Asistent"
    assert (task["events"][3]["from_assignee"], task["events"][3]["to_assignee"]) == ("Asistent", "Medic")
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CollabTaskEvent)) == 5


@pytest.mark.asyncio
async def test_task_cancel_needs_a_reason_and_only_a_manager_reopens(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    task = await new_task(client, doctor, doctor["id"])
    assert (await change(client, doctor, task, status="cancelled")).status_code == 422
    assert (await change(client, doctor, task, status="open")).status_code == 422
    task = (await change(client, doctor, task, status="cancelled", reason="Nu mai e nevoie")).json()
    assert (await change(client, doctor, task, status="in_progress")).status_code == 409
    assert (await change(client, doctor, task, status="open", reason="Greșit")).status_code == 403
    assert (await change(client, c, task, status="open")).status_code == 422
    reopened = await change(client, c, task, status="open", reason="Era încă necesară")
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"


@pytest.mark.asyncio
async def test_stale_task_version_is_409_and_changes_nothing(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    task = await new_task(client, doctor, doctor["id"])
    assert (await change(client, doctor, task, status="in_progress")).status_code == 200
    stale = await change(client, doctor, task, status="resolved")
    assert stale.status_code == 409 and "modificată" in stale.json()["detail"]
    async with sessions() as session:
        assert (await session.get(CollabTask, UUID(task["id"]))).status == "in_progress"


@pytest.mark.asyncio
async def test_task_visibility_assignment_and_clinic_isolation(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    doctor, assistant, outsider = [await person(sessions, c["clinic_id"], role) for role in ("doctor", "assistant", "reception")]
    stranger = await person(sessions, other["clinic_id"], "doctor")
    task = await new_task(client, doctor, assistant["id"])
    assert (await change(client, outsider, task, status="resolved")).status_code == 404
    assert (await change(client, other, task, status="resolved")).status_code == 404
    assert (await client.get("/api/collab/tasks", headers=outsider["auth"])).json() == []
    assert len((await client.get("/api/collab/tasks", headers=c["auth"])).json()) == 1
    assert (await client.get("/api/collab/tasks", headers=other["auth"])).json() == []
    assert (await client.post("/api/collab/tasks", headers=doctor["auth"], json={"title": "T", "assignee_id": stranger["id"]})).status_code == 404
    assert (await change(client, doctor, task, assignee_id=stranger["id"])).status_code == 404
    await deactivate(sessions, outsider["id"])
    assert (await client.post("/api/collab/tasks", headers=doctor["auth"], json={"title": "T", "assignee_id": outsider["id"]})).status_code == 422
    assert (await change(client, doctor, task, assignee_id=outsider["id"])).status_code == 422


@pytest.mark.asyncio
async def test_task_due_uses_clinic_time_including_dst(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    winter = await new_task(client, doctor, doctor["id"], local_due="2030-01-15T17:00:00")
    assert winter["due_at"] == "2030-01-15T15:00:00Z"
    gap = await client.post("/api/collab/tasks", headers=doctor["auth"], json={"title": "T", "assignee_id": doctor["id"], "local_due": "2030-03-31T03:30:00"})
    assert gap.status_code == 422
    ambiguous = {"title": "T", "assignee_id": doctor["id"], "local_due": "2030-10-27T03:30:00"}
    assert (await client.post("/api/collab/tasks", headers=doctor["auth"], json=ambiguous)).status_code == 422
    first = await new_task(client, doctor, doctor["id"], local_due="2030-10-27T03:30:00", due_fold=0)
    second = await new_task(client, doctor, doctor["id"], local_due="2030-10-27T03:30:00", due_fold=1)
    assert (first["due_at"], second["due_at"]) == ("2030-10-27T00:30:00Z", "2030-10-27T01:30:00Z")
    assert (await client.post("/api/collab/tasks", headers=doctor["auth"], json={"title": "T", "assignee_id": doctor["id"], "local_due": "2020-01-01T10:00:00"})).status_code == 422
    assert (await client.post("/api/collab/tasks", headers=doctor["auth"], json={"title": "T", "assignee_id": doctor["id"], "local_due": "2030-01-15T17:00:00+02:00"})).status_code == 422


@pytest.mark.asyncio
async def test_task_filters_mine_overdue_and_status(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant = await person(sessions, c["clinic_id"], "doctor"), await person(sessions, c["clinic_id"], "assistant")
    mine = await new_task(client, doctor, doctor["id"])
    theirs = await new_task(client, doctor, assistant["id"])
    late = await new_task(client, doctor, doctor["id"])
    closed_late = await new_task(client, doctor, doctor["id"])
    async with sessions() as session:
        for task_id, state in ((late["id"], "in_progress"), (closed_late["id"], "resolved")):
            row = await session.get(CollabTask, UUID(task_id))
            row.due_at, row.status = datetime.now(UTC) - timedelta(hours=1), state
        await session.commit()
    ids = lambda response: {row["id"] for row in response.json()}
    url = "/api/collab/tasks"
    assert ids(await client.get(url, headers=doctor["auth"], params={"mine": "true"})) == {mine["id"], late["id"], closed_late["id"]}
    overdue = await client.get(url, headers=doctor["auth"], params={"overdue": "true"})
    assert ids(overdue) == {late["id"]} and overdue.json()[0]["overdue"] is True
    assert ids(await client.get(url, headers=doctor["auth"], params={"status": "open"})) == {mine["id"], theirs["id"]}
    assert ids(await client.get(url, headers=doctor["auth"], params={"mine": "true", "status": "resolved"})) == {closed_late["id"]}
    assert (await client.get(url, headers=doctor["auth"], params={"status": "nimic"})).status_code == 422


# ---------------------------------------------------------------------------------------------
# Handovers
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handover_freezes_unresolved_tasks_and_the_recipient_confirms(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, next_doctor, outsider = [await person(sessions, c["clinic_id"], role) for role in ("doctor", "doctor", "reception")]
    shift_id = await on_shift(sessions, c, doctor["id"], "doctor")
    kept = await new_task(client, doctor, doctor["id"])
    done = await new_task(client, doctor, doctor["id"])
    await change(client, doctor, done, status="resolved")
    await new_task(client, doctor, next_doctor["id"])
    body = {"shift_id": shift_id, "to_user_id": next_doctor["id"], "notes": "Note fictive de predare"}
    created = await client.post("/api/collab/handovers", headers=doctor["auth"], json=body)
    assert created.status_code == 201, created.text
    handover = created.json()
    assert [row["task_id"] for row in handover["tasks"]] == [kept["id"]] and handover["tasks"][0]["status"] == "open" and handover["acknowledged_at"] is None
    assert (await client.get("/api/collab/summary", headers=next_doctor["auth"])).json()["pending_handovers"] == 1
    assert (await client.post("/api/collab/handovers", headers=doctor["auth"], json={**body, "notes": "Rescris"})).status_code == 409
    await change(client, doctor, kept, status="resolved")
    listed = (await client.get("/api/collab/handovers", headers=next_doctor["auth"])).json()
    assert listed[0]["notes"] == "Note fictive de predare" and listed[0]["tasks"][0]["status"] == "open"
    url = f"/api/collab/handovers/{handover['id']}/acknowledge"
    assert (await client.post(url, headers=doctor["auth"])).status_code == 403
    assert (await client.post(url, headers=c["auth"])).status_code == 403
    assert (await client.post(url, headers=outsider["auth"])).status_code == 404
    first = await client.post(url, headers=next_doctor["auth"])
    again = await client.post(url, headers=next_doctor["auth"])
    assert first.status_code == again.status_code == 200 and first.json()["acknowledged_at"] == again.json()["acknowledged_at"] is not None
    assert (await client.get("/api/collab/summary", headers=next_doctor["auth"])).json()["pending_handovers"] == 0
    assert (await client.get("/api/collab/handovers", headers=outsider["auth"])).json() == []


@pytest.mark.asyncio
async def test_handover_validates_shift_person_and_clinic(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    doctor, assistant, gone = [await person(sessions, c["clinic_id"], role) for role in ("doctor", "assistant", "reception")]
    stranger = await person(sessions, other["clinic_id"], "doctor")
    shift_id = await on_shift(sessions, c, doctor["id"], "doctor")
    future = await on_shift(sessions, c, assistant["id"], "assistant", start=2, end=4)
    post = lambda who, shift, to: client.post("/api/collab/handovers", headers=who["auth"], json={"shift_id": shift, "to_user_id": to, "notes": "Note"})
    assert (await post(assistant, shift_id, gone["id"])).status_code == 404  # someone else's shift
    assert (await post(other, shift_id, stranger["id"])).status_code == 404
    assert (await post(doctor, shift_id, stranger["id"])).status_code == 404
    assert (await post(doctor, shift_id, doctor["id"])).status_code == 422
    assert (await post(assistant, future, doctor["id"])).status_code == 422
    await deactivate(sessions, gone["id"])
    assert (await post(doctor, shift_id, gone["id"])).status_code == 422
    async with sessions() as session:
        row = await session.get(TeamShift, UUID(shift_id))
        row.status, row.cancelled_at, row.cancelled_by_id, row.cancel_reason = "cancelled", datetime.now(UTC), UUID(c["id"]), "Anulată"
        await session.commit()
    assert (await post(doctor, shift_id, assistant["id"])).status_code == 409
    managed = await on_shift(sessions, c, assistant["id"], "assistant", start=-5, end=-2)
    by_manager = await post(c, managed, doctor["id"])
    assert by_manager.status_code == 201 and by_manager.json()["from_user_id"] == assistant["id"] and by_manager.json()["created_by"] == "Manager Fictiv"


# ---------------------------------------------------------------------------------------------
# Taking over tasks from a handover
# ---------------------------------------------------------------------------------------------


async def handed_over(client, sessions, c, sender, receiver, *tasks):
    shift_id = await on_shift(sessions, c, sender["id"], "doctor")
    created = await client.post("/api/collab/handovers", headers=sender["auth"], json={"shift_id": shift_id, "to_user_id": receiver["id"], "notes": "Note fictive"})
    assert created.status_code == 201, created.text
    return created.json()


def take_url(handover, task):
    return f"/api/collab/handovers/{handover['id']}/tasks/{task['id']}/take"


async def acknowledge(client, who, handover):
    response = await client.post(f"/api/collab/handovers/{handover['id']}/acknowledge", headers=who["auth"])
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_handover_recipient_reads_takes_and_then_works_on_the_task(env):
    client, sessions = env
    c = await clinic(client, sessions)
    sender, receiver, outsider = [await person(sessions, c["clinic_id"], role, name) for role, name in (("doctor", "Medic Pleacă"), ("doctor", "Medic Vine"), ("reception", "Coleg"))]
    task = await new_task(client, sender, sender["id"], description="Descriere fictivă")
    handover = await handed_over(client, sessions, c, sender, receiver)
    assert [row["id"] for row in (await client.get("/api/collab/tasks", headers=receiver["auth"])).json()] == [task["id"]]
    assert (await client.get("/api/collab/tasks", headers=receiver["auth"], params={"mine": "true"})).json() == []
    assert (await client.get("/api/collab/tasks", headers=outsider["auth"])).json() == []
    assert (await change(client, receiver, task, status="resolved")).status_code == 403
    assert (await change(client, outsider, task, status="resolved")).status_code == 404
    assert (await client.post(take_url(handover, task), headers=receiver["auth"], json={"expected_version": 1})).status_code == 409  # receipt comes first
    confirmed = await acknowledge(client, receiver, handover)
    assert confirmed["tasks"][0]["can_take"] is True and confirmed["tasks"][0]["version"] == 1
    assert (await client.post(take_url(handover, task), headers=outsider["auth"], json={"expected_version": 1})).status_code == 404
    assert (await client.post(take_url(handover, task), headers=sender["auth"], json={"expected_version": 1})).status_code == 403
    assert (await client.post(take_url(handover, task), headers=c["auth"], json={"expected_version": 1})).status_code == 403
    assert (await client.post(take_url(handover, task), headers=receiver["auth"], json={"expected_version": 7})).status_code == 409
    taken = await client.post(take_url(handover, task), headers=receiver["auth"], json={"expected_version": 1})
    assert taken.status_code == 200, taken.text
    body = taken.json()
    assert (body["assignee"], body["version"], body["status"]) == ("Medic Vine", 2, "open")
    event = body["events"][-1]
    assert (event["from_assignee"], event["to_assignee"], event["actor"]) == ("Medic Pleacă", "Medic Vine", "Medic Vine") and "predare" in event["reason"].lower()
    assert (await client.post(take_url(handover, task), headers=receiver["auth"], json={"expected_version": 2})).status_code == 409
    worked = await change(client, receiver, body, status="resolved")
    assert worked.status_code == 200 and worked.json()["status"] == "resolved"
    frozen = (await client.get("/api/collab/handovers", headers=receiver["auth"])).json()[0]["tasks"][0]
    assert frozen["status"] == "open" and frozen["title"] == "Sarcină fictivă" and frozen["can_take"] is False


@pytest.mark.asyncio
async def test_task_cannot_be_taken_once_resolved_or_moved_to_someone_else(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    sender, receiver, third = [await person(sessions, c["clinic_id"], "doctor") for _ in range(3)]
    done, moved = await new_task(client, sender, sender["id"]), await new_task(client, sender, sender["id"])
    handover = await handed_over(client, sessions, c, sender, receiver)
    unrelated = await new_task(client, sender, sender["id"])  # created after the handover: not in its list
    await acknowledge(client, receiver, handover)
    done = (await change(client, sender, done, status="resolved")).json()
    moved = (await change(client, sender, moved, assignee_id=third["id"])).json()
    assert (await client.post(take_url(handover, done), headers=receiver["auth"], json={"expected_version": done["version"]})).status_code == 409
    assert (await client.post(take_url(handover, moved), headers=receiver["auth"], json={"expected_version": moved["version"]})).status_code == 409
    assert (await client.post(take_url(handover, unrelated), headers=receiver["auth"], json={"expected_version": 1})).status_code == 404
    assert (await client.post(take_url(handover, moved), headers=other["auth"], json={"expected_version": moved["version"]})).status_code == 404
    listed = (await client.get("/api/collab/handovers", headers=receiver["auth"])).json()[0]["tasks"]
    assert all(row["can_take"] is False and row["version"] is None for row in listed)
    assert unrelated["id"] not in {row["id"] for row in (await client.get("/api/collab/tasks", headers=receiver["auth"])).json()}


# ---------------------------------------------------------------------------------------------
# Long history
# ---------------------------------------------------------------------------------------------


async def page(client, who, conversation_id, **params):
    response = await client.get(f"/api/collab/conversations/{conversation_id}/messages", headers=who["auth"], params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_before_seq_pages_back_to_the_very_first_message(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant = await person(sessions, c["clinic_id"], "doctor"), await person(sessions, c["clinic_id"], "assistant")
    item = await direct(client, doctor, assistant["id"])
    async with sessions() as session:
        now = datetime.now(UTC)
        session.add_all([CollabMessage(clinic_id=UUID(c["clinic_id"]), conversation_id=UUID(item["id"]), seq=seq, sender_id=UUID(doctor["id"]), body=f"Mesaj {seq}", client_request_id=uuid4(), created_at=now) for seq in range(1, 511)])
        (await session.get(CollabConversation, UUID(item["id"]))).last_seq = 510
        await session.commit()
    first = await page(client, assistant, item["id"])
    assert [row["seq"] for row in first["messages"]] == list(range(461, 511)) and first["has_more"] is True and first["oldest_unread_seq"] == 1
    seen = [row["seq"] for row in first["messages"]]
    current = first
    while current["has_more"]:
        current = await page(client, assistant, item["id"], before_seq=current["messages"][0]["seq"], limit=200)
        seen = [row["seq"] for row in current["messages"]] + seen
    assert seen == list(range(1, 511))
    assert (await client.get(f"/api/collab/conversations/{item['id']}/messages", headers=assistant["auth"], params={"before_seq": 0})).status_code == 422
    assert (await page(client, doctor, item["id"]))["oldest_unread_seq"] is None  # own messages are never unread
    await client.post(f"/api/collab/conversations/{item['id']}/read", headers=assistant["auth"], json={"seq": 500})
    assert (await page(client, assistant, item["id"]))["oldest_unread_seq"] == 501


@pytest.mark.asyncio
async def test_before_seq_and_oldest_unread_follow_room_visibility_with_gaps(env):
    client, sessions = env
    c = await clinic(client, sessions)
    reception, first, second = [await person(sessions, c["clinic_id"], role) for role in ("reception", "doctor", "assistant")]
    first_shift = await on_shift(sessions, c, first["id"], "doctor")
    to_room = lambda body: client.post(f"/api/collab/rooms/{c['room']}/messages", headers=reception["auth"], json=message(body))
    conversation = (await to_room("Unu")).json()["conversation_id"]
    await on_shift(sessions, c, second["id"], "assistant")
    await to_room("Doi")
    async with sessions() as session:
        row = await session.get(TeamShift, UUID(first_shift))
        row.status, row.cancelled_at, row.cancelled_by_id, row.cancel_reason = "cancelled", datetime.now(UTC), UUID(c["id"]), "Schimb"
        await session.commit()
    await to_room("Trei")
    seqs = lambda body: [row["seq"] for row in body["messages"]]
    latest = await page(client, second, conversation, limit=1)
    assert seqs(latest) == [3] and latest["has_more"] is True and latest["oldest_unread_seq"] == 2
    older = await page(client, second, conversation, before_seq=3)
    assert seqs(older) == [2] and older["has_more"] is False
    assert seqs(await page(client, first, conversation, before_seq=3)) == [1, 2]
    assert (await page(client, first, conversation))["oldest_unread_seq"] == 1


# ---------------------------------------------------------------------------------------------
# User row locks (real concurrency is in test_postgres_collab.py)
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collab_locks_users_for_share_on_every_write_path(env, monkeypatch):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor, assistant, reception = [await person(sessions, c["clinic_id"], role) for role in ("doctor", "assistant", "reception")]
    seen: list[str] = []
    original_scalar = AsyncSession.scalar

    async def recording_scalar(self, statement, *args, **kwargs):
        if hasattr(statement, "compile"):
            sql = str(statement.compile(dialect=postgresql.dialect()))
            if "FROM users" in sql and " FOR " in sql:
                seen.append(sql)
        return await original_scalar(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalar", recording_scalar)
    shift_id = await on_shift(sessions, c, doctor["id"], "doctor")
    await on_shift(sessions, c, assistant["id"], "assistant")
    conversation = await direct(client, doctor, assistant["id"])
    assert (await send(client, doctor, conversation["id"])).status_code == 201
    assert (await client.post(f"/api/collab/rooms/{c['room']}/messages", headers=doctor["auth"], json=message())).status_code == 201
    group = await client.post("/api/collab/conversations", headers=doctor["auth"], json={"kind": "group", "title": "G", "member_ids": [assistant["id"]]})
    assert (await client.post(f"/api/collab/conversations/{group.json()['id']}/members", headers=doctor["auth"], json={"user_id": reception["id"]})).status_code == 201
    task = await new_task(client, doctor, doctor["id"])
    other = await new_task(client, doctor, doctor["id"])
    assert (await change(client, doctor, other, assignee_id=assistant["id"])).status_code == 200
    created = await client.post("/api/collab/handovers", headers=doctor["auth"], json={"shift_id": shift_id, "to_user_id": assistant["id"], "notes": "Note"})
    await acknowledge(client, assistant, created.json())
    assert (await client.post(take_url(created.json(), task), headers=assistant["auth"], json={"expected_version": task["version"]})).status_code == 200
    assert len(seen) >= 8
    assert all("FOR SHARE" in sql and "FOR UPDATE" not in sql for sql in seen), [sql[-40:] for sql in seen if "FOR SHARE" not in sql]

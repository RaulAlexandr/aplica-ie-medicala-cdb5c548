"""HTTP regressions for clinical note content validation."""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import AuditEvent, Base, ClinicalNote, ClinicalNoteRevision, get_db
from app.main import app


@pytest.fixture
async def note_context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            registration = await client.post(
                "/api/auth/register",
                json={
                    "clinic_name": "Note Validation Clinic",
                    "full_name": "Note Author",
                    "email": "note-author@example.com",
                    "password": "correct horse battery staple",
                },
            )
            assert registration.status_code == 201
            headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
            patient_response = await client.post(
                "/api/patients", headers=headers, json={"first_name": "Fictitious", "last_name": "Patient"}
            )
            assert patient_response.status_code == 201
            yield client, headers, patient_response.json()["id"], sessions
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def create_note(client, headers, patient_id, content="Valid note text"):
    return await client.post(
        "/api/clinical-notes",
        headers=headers,
        json={"patient_id": patient_id, "content": content},
    )


async def make_valid_note(note_context, content="Valid note text"):
    client, headers, patient_id, sessions = note_context
    response = await create_note(client, headers, patient_id, content)
    assert response.status_code == 201
    return client, headers, patient_id, sessions, response.json()


@pytest.mark.parametrize("content", [None, "", "   \t\n"])
async def test_create_rejects_null_empty_and_whitespace_content(note_context, content):
    client, headers, patient_id, _sessions = note_context
    response = await create_note(client, headers, patient_id, content)
    assert response.status_code == 422


@pytest.mark.parametrize("content", [None, "", "   \t\n"])
async def test_patch_rejects_null_empty_and_whitespace_without_mutations(note_context, content):
    client, headers, _patient_id, sessions, note = await make_valid_note(note_context)
    note_id = note["id"]
    async with sessions() as session:
        persisted_before = await session.get(ClinicalNote, note_id)
        before_updated_at = persisted_before.updated_at
        before_revisions = await session.scalar(
            select(func.count()).select_from(ClinicalNoteRevision).where(
                ClinicalNoteRevision.clinical_note_id == note_id
            )
        )
        before_audits = await session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.entity_type == "clinical_note", AuditEvent.entity_id == note_id
            )
        )

    response = await client.patch(
        f"/api/clinical-notes/{note_id}", headers=headers, json={"content": content}
    )
    assert response.status_code == 422
    async with sessions() as session:
        persisted_after = await session.get(ClinicalNote, note_id)
        assert persisted_after.content == "Valid note text"
        assert persisted_after.updated_at == before_updated_at
        assert await session.scalar(
            select(func.count()).select_from(ClinicalNoteRevision).where(
                ClinicalNoteRevision.clinical_note_id == note_id
            )
        ) == before_revisions
        assert await session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.entity_type == "clinical_note", AuditEvent.entity_id == note_id
            )
        ) == before_audits


async def test_patch_without_content_preserves_text_and_valid_text_round_trips(note_context):
    valid_text = "  First line\nSecond line  "
    client, headers, _patient_id, sessions, note = await make_valid_note(note_context, valid_text)
    note_id = note["id"]
    title_update = await client.patch(
        f"/api/clinical-notes/{note_id}", headers=headers, json={"title": "Reviewed"}
    )
    assert title_update.status_code == 200
    assert title_update.json()["content"] == valid_text

    content_update = "  Edited line\nSecond edited line  "
    edited = await client.patch(
        f"/api/clinical-notes/{note_id}", headers=headers, json={"content": content_update}
    )
    assert edited.status_code == 200
    assert edited.json()["content"] == content_update
    finalized = await client.post(f"/api/clinical-notes/{note_id}/finalize", headers=headers)
    assert finalized.status_code == 200
    assert finalized.json()["content"] == content_update
    assert finalized.json()["status"] == "finalized"
    async with sessions() as session:
        persisted = await session.get(ClinicalNote, note_id)
        assert persisted.content == content_update


@pytest.mark.parametrize("legacy_content", ["", "   \t\n"])
async def test_finalize_rejects_legacy_empty_content_without_mutations(note_context, legacy_content):
    client, headers, _patient_id, sessions, note = await make_valid_note(note_context)
    note_id = note["id"]
    legacy_updated_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    async with sessions.begin() as session:
        legacy = await session.get(ClinicalNote, note_id)
        legacy.content = legacy_content
        legacy.updated_at = legacy_updated_at
        legacy.created_at = datetime(2026, 1, 1, 1, 2, 3, tzinfo=UTC)

    async with sessions() as session:
        before = await session.get(ClinicalNote, note_id)
        before_revisions = await session.scalar(
            select(func.count()).select_from(ClinicalNoteRevision).where(
                ClinicalNoteRevision.clinical_note_id == note_id
            )
        )
        before_audits = await session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.entity_type == "clinical_note", AuditEvent.entity_id == note_id
            )
        )
        before_snapshot = (before.content, before.status, before.updated_at, before.finalized_at)

    response = await client.post(f"/api/clinical-notes/{note_id}/finalize", headers=headers)
    assert response.status_code == 422
    async with sessions() as session:
        after = await session.get(ClinicalNote, note_id)
        assert (after.content, after.status, after.updated_at, after.finalized_at) == before_snapshot
        assert after.created_at.replace(tzinfo=UTC) == datetime(2026, 1, 1, 1, 2, 3, tzinfo=UTC)
        assert await session.scalar(
            select(func.count()).select_from(ClinicalNoteRevision).where(
                ClinicalNoteRevision.clinical_note_id == note_id
            )
        ) == before_revisions
        assert await session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.entity_type == "clinical_note", AuditEvent.entity_id == note_id
            )
        ) == before_audits

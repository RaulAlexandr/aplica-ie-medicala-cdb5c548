"""T11 protected documents/images: authorization, clinic isolation, references, formats, limits,
dangerous names, storage/DB failure rollback, idempotency and persistence."""

import os
import stat
import struct
import zlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.config import settings
from app.database import (
    AuditEvent,
    Base,
    ClinicalDocument,
    PerformedProcedure,
    ProcedureExecutionSegment,
    User,
    get_db,
)
from app.documents import storage
from app.main import app

PASSWORD = "correct horse battery staple"


def png(width: int = 4, height: int = 3) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(width: int = 640, height: int = 480) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def pdf(extra: bytes = b"") -> bytes:
    return (b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R " + extra + b">> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n")


class FlakySession(AsyncSession):
    fail_commit = False

    async def commit(self) -> None:
        if FlakySession.fail_commit:
            FlakySession.fail_commit = False
            raise OperationalError("COMMIT", {}, Exception("simulated database failure"))
        await super().commit()


@pytest.fixture
async def env(tmp_path, monkeypatch):
    store = tmp_path / "t11-store"
    monkeypatch.setattr(settings, "document_storage_dir", str(store))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=FlakySession)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions, store
    app.dependency_overrides.clear()
    FlakySession.fail_commit = False
    await engine.dispose()


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def clinic(client, email: str) -> dict:
    response = await client.post("/api/auth/register", json={
        "clinic_name": "Clinica Fictivă T11", "full_name": email.split("@")[0], "email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    auth = h(response.json()["access_token"])
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = await client.post("/api/rooms", headers=auth, json={"name": "Cabinet T11"})
    return {"auth": auth, "me": me, "room_id": room.json()["id"]}


async def add_user(sessions, clinic_id: str, role: str) -> dict[str, str]:
    user = User(id=uuid4(), clinic_id=UUID(clinic_id), email=f"{role}-{uuid4().hex[:6]}@example.com",
                password_hash="unused", full_name=f"{role} fictiv", role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
    return h(create_access_token(user))


async def patient(client, auth, first="Ana") -> str:
    response = await client.post("/api/patients", headers=auth, json={"first_name": first, "last_name": "Fictiv"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def visit(client, ctx, patient_id: str, day: int = 1) -> str:
    response = await client.post("/api/appointments", headers=ctx["auth"], json={
        "patient_id": patient_id, "doctor_id": ctx["me"]["id"], "room_id": ctx["room_id"],
        "starts_at": f"2026-12-{day:02d}T09:00:00+00:00", "duration_minutes": 30, "appointment_type": "Control"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def upload(client, auth, patient_id, content: bytes, name="poza.png", *, category="photo",
                 key=None, description=None, **params):
    headers = {**auth, "Content-Type": "application/octet-stream", "X-File-Name": storage_quote(name)}
    if key:
        headers["Idempotency-Key"] = key
    if description is not None:
        headers["X-Document-Description"] = storage_quote(description)
    query = {"category": category, **{k: v for k, v in params.items() if v is not None}}
    return await client.post(f"/api/documents/patients/{patient_id}", headers=headers, params=query, content=content)


def storage_quote(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


async def doc_count(sessions) -> int:
    async with sessions() as session:
        return await session.scalar(select(func.count()).select_from(ClinicalDocument))


def stored_files(store: Path) -> list[Path]:
    return [p for p in store.rglob("*") if p.is_file()] if store.exists() else []


async def test_doctor_uploads_image_with_visit_and_tooth_and_reads_it_back(env):
    client, sessions, store = env
    ctx = await clinic(client, "t11-owner@example.com")
    doctor = await add_user(sessions, ctx["me"]["clinic_id"], "doctor")
    pid = await patient(client, ctx["auth"])
    vid = await visit(client, ctx, pid)
    body = png(5, 7)
    response = await upload(client, doctor, pid, body, "Radiografie 16.png", category="radiograph",
                            appointment_id=vid, tooth_fdi="16", clinical_date="2026-11-18", description="Control fictiv")
    assert response.status_code == 201, response.text
    doc = response.json()
    assert (doc["content_type"], doc["width"], doc["height"], doc["size_bytes"]) == ("image/png", 5, 7, len(body))
    assert (doc["tooth_fdi"], doc["appointment"]["id"], doc["clinical_date"]) == ("16", vid, "2026-11-18")
    assert doc["original_filename"] == "Radiografie 16.png" and doc["description"] == "Control fictiv"
    assert doc["uploaded_by"]["name"] == "doctor fictiv" and doc["uploaded_at"] and doc["status"] == "active"
    assert "storage_key" not in doc and str(store) not in response.text

    files = stored_files(store)
    assert len(files) == 1 and "Radiografie" not in str(files[0])
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert files[0].read_bytes() == body

    content = await client.get(f"/api/documents/{doc['id']}/content", headers=doctor)
    assert content.status_code == 200 and content.content == body
    assert content.headers["content-type"] == "image/png"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in content.headers["cache-control"]
    assert "sandbox" in content.headers["content-security-policy"]
    assert content.headers["content-disposition"].startswith("inline;")
    download = await client.get(f"/api/documents/{doc['id']}/content", params={"download": "true"}, headers=doctor)
    assert download.headers["content-disposition"].startswith("attachment;")

    listed = await client.get(f"/api/documents/patients/{pid}", headers=doctor)
    assert [row["id"] for row in listed.json()] == [doc["id"]]
    async with sessions() as session:
        actions = sorted(await session.scalars(select(AuditEvent.action).where(AuditEvent.entity_type == "clinical_document")))
    assert actions == ["downloaded", "uploaded", "viewed"]


async def test_manager_admin_allowed_reception_assistant_denied_including_direct_content(env):
    client, sessions, _ = env
    ctx = await clinic(client, "t11-roles@example.com")
    cid = ctx["me"]["clinic_id"]
    admin = await add_user(sessions, cid, "administrator")
    pid = await patient(client, ctx["auth"])
    created = await upload(client, ctx["auth"], pid, jpeg(), "intraoral.jpg")
    assert created.status_code == 201, created.text
    doc_id = created.json()["id"]
    for allowed in (ctx["auth"], admin):
        assert (await client.get(f"/api/documents/patients/{pid}", headers=allowed)).status_code == 200
        assert (await client.get(f"/api/documents/{doc_id}/content", headers=allowed)).status_code == 200
    admin_upload = await upload(client, admin, pid, pdf(), "consimtamant.pdf", category="document")
    assert admin_upload.status_code == 201, admin_upload.text
    for role in ("reception", "assistant"):
        denied = await add_user(sessions, cid, role)
        assert (await client.get("/api/documents/limits", headers=denied)).status_code == 403
        assert (await client.get(f"/api/documents/patients/{pid}", headers=denied)).status_code == 403
        assert (await client.get(f"/api/documents/{doc_id}", headers=denied)).status_code == 403
        assert (await client.get(f"/api/documents/{doc_id}/content", headers=denied)).status_code == 403
        assert (await upload(client, denied, pid, png())).status_code == 403
        assert (await client.post(f"/api/documents/{doc_id}/archive", headers=denied, json={"reason": "x"})).status_code == 403
    assert (await client.get(f"/api/documents/{doc_id}/content")).status_code == 401
    assert await doc_count(sessions) == 2


async def test_other_clinic_and_foreign_visit_are_refused_without_partial_records(env):
    client, sessions, store = env
    a = await clinic(client, "t11-a@example.com")
    b = await clinic(client, "t11-b@example.com")
    pa = await patient(client, a["auth"], "Ana")
    pa2 = await patient(client, a["auth"], "Bianca")
    pb = await patient(client, b["auth"], "Corina")
    va2 = await visit(client, a, pa2)
    vb = await visit(client, b, pb)
    doc = (await upload(client, a["auth"], pa, png())).json()

    assert (await client.get(f"/api/documents/patients/{pa}", headers=b["auth"])).status_code == 404
    assert (await client.get(f"/api/documents/{doc['id']}", headers=b["auth"])).status_code == 404
    assert (await client.get(f"/api/documents/{doc['id']}/content", headers=b["auth"])).status_code == 404
    assert (await client.post(f"/api/documents/{doc['id']}/archive", headers=b["auth"], json={"reason": "x"})).status_code == 404
    assert (await upload(client, b["auth"], pa, png())).status_code == 404
    assert (await upload(client, a["auth"], pa, png(), appointment_id=va2)).status_code == 422  # other patient's visit
    assert (await upload(client, a["auth"], pa, png(), appointment_id=vb)).status_code == 422  # other clinic's visit
    assert (await upload(client, a["auth"], pa, png(), appointment_id=str(uuid4()))).status_code == 422
    for tooth in ("19", "51", "99", "1"):
        assert (await upload(client, a["auth"], pa, png(), tooth_fdi=tooth)).status_code == 422
    assert (await upload(client, a["auth"], pa, png(), clinical_date="2099-01-01")).status_code == 422
    assert await doc_count(sessions) == 1
    assert len(stored_files(store)) == 1


@pytest.mark.parametrize(("content", "name", "category", "expected"), [
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "x.svg", "photo", 415),
    (b"<!doctype html><html><script>alert(1)</script></html>", "x.png", "photo", 415),
    (b"GIF89a\x01\x00\x01\x00", "x.gif", "photo", 415),
    (png(), "deghizat.pdf", "document", 415),  # extension does not match real content
    (pdf(), "deghizat.png", "document", 415),
    (pdf(b"/OpenAction << /S /JavaScript /JS (app.alert(1)) >>"), "activ.pdf", "document", 415),
    (pdf(b"/OpenAction << /S /J#61vaScript >>"), "ascuns.pdf", "document", 415),
    (pdf(b"/Names << /EmbeddedFiles 3 0 R >>"), "atasat.pdf", "document", 415),
    (b"%PDF-1.4\n1 0 obj << >> endobj\n", "trunchiat.pdf", "document", 415),
    (png()[:-12], "trunchiat.png", "photo", 415),
    (b"\xff\xd8\xff\xd9", "fara-dimensiuni.jpg", "photo", 415),
    (pdf(), "raport.pdf", "radiograph", 422),  # radiographs/photos must be raster images
    (b"", "gol.png", "photo", 422),
])
async def test_real_type_detection_and_active_content_are_enforced(env, content, name, category, expected):
    client, sessions, store = env
    ctx = await clinic(client, f"t11-type-{uuid4().hex[:6]}@example.com")
    pid = await patient(client, ctx["auth"])
    response = await upload(client, ctx["auth"], pid, content, name, category=category)
    assert response.status_code == expected, response.text
    assert await doc_count(sessions) == 0
    assert stored_files(store) == []


async def test_size_limit_is_enforced_while_streaming_and_image_dimensions_are_bounded(env, monkeypatch):
    client, sessions, store = env
    ctx = await clinic(client, "t11-limits@example.com")
    pid = await patient(client, ctx["auth"])
    limits = (await client.get("/api/documents/limits", headers=ctx["auth"])).json()
    assert limits["max_bytes"] == 20 * 1024 * 1024 and limits["antivirus_scan"] is False

    monkeypatch.setattr(settings, "document_max_bytes", 1024)
    body = png() + b"\x00" * 2048

    async def chunked():  # no Content-Length: the limit must be applied during the read
        for start in range(0, len(body), 256):
            yield body[start:start + 256]

    headers = {**ctx["auth"], "Content-Type": "application/octet-stream", "X-File-Name": "mare.png"}
    streamed = await client.post(f"/api/documents/patients/{pid}", params={"category": "photo"},
                                 headers=headers, content=chunked())
    assert "content-length" not in {k.lower() for k in streamed.request.headers}
    assert streamed.status_code == 413, streamed.text
    declared = await upload(client, ctx["auth"], pid, body, "mare.png")
    assert declared.status_code == 413

    monkeypatch.setattr(settings, "document_max_bytes", 20 * 1024 * 1024)
    monkeypatch.setattr(settings, "document_max_image_side", 1000)
    assert (await upload(client, ctx["auth"], pid, jpeg(1200, 800), "lat.jpg")).status_code == 413
    monkeypatch.setattr(settings, "document_max_image_pixels", 100)
    assert (await upload(client, ctx["auth"], pid, png(20, 20), "multi.png")).status_code == 413
    assert await doc_count(sessions) == 0 and stored_files(store) == []


async def test_dangerous_filenames_are_display_metadata_only(env):
    client, _sessions, store = env
    ctx = await clinic(client, "t11-names@example.com")
    pid = await patient(client, ctx["auth"])
    cases = {
        "../../../etc/passwd.png": "passwd.png",
        "..\\..\\windows\\evil.png": "evil.png",
        "<img src=x onerror=alert(1)>.png": "<img src=x onerror=alert(1)>.png",
        "rând\r\nX-Injected: 1\u202e.png": "rândX-Injected: 1.png",
        "\"quoted\";.png": "\"quoted\";.png",
    }
    for raw, expected in cases.items():
        response = await upload(client, ctx["auth"], pid, png(), raw)
        assert response.status_code == 201, (raw, response.text)
        doc = response.json()
        assert doc["original_filename"] == expected
        content = await client.get(f"/api/documents/{doc['id']}/content", headers=ctx["auth"])
        disposition = content.headers["content-disposition"]
        assert "\r" not in disposition and "\n" not in disposition
        assert disposition.split('filename="', 1)[1].split('"', 1)[0].isascii()
    for path in stored_files(store):
        assert path.parent.parent.parent == store and len(path.name) == 32
        assert all(ch in "0123456789abcdef" for ch in path.name)
    assert not (store.parent / "etc").exists()


async def test_storage_failure_and_db_failure_leave_no_row_and_no_file(env, monkeypatch):
    client, sessions, store = env
    ctx = await clinic(client, "t11-rollback@example.com")
    pid = await patient(client, ctx["auth"])

    def broken_place(*_args, **_kwargs):
        raise OSError("disk full (simulated)")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "place", broken_place)
        response = await upload(client, ctx["auth"], pid, png())
    assert response.status_code == 503, response.text
    assert await doc_count(sessions) == 0 and stored_files(store) == []

    FlakySession.fail_commit = True
    with pytest.raises(OperationalError):
        await upload(client, ctx["auth"], pid, png())
    assert await doc_count(sessions) == 0 and stored_files(store) == []

    # A storage key collision must never overwrite an existing clinical file.
    monkeypatch.setattr(storage, "new_storage_key", lambda: "a" * 32)
    first = await upload(client, ctx["auth"], pid, png(2, 2), "prima.png")
    assert first.status_code == 201
    second = await upload(client, ctx["auth"], pid, png(3, 3), "a-doua.png")
    assert second.status_code == 409
    assert await doc_count(sessions) == 1
    files = stored_files(store)
    assert len(files) == 1 and files[0].read_bytes() == png(2, 2)
    # The storage layer itself refuses to replace an existing key (os.link, never rename-over).
    staged = storage.StagedFile(path=store / storage.INCOMING_DIR / "manual.part", size=3, sha256="x")
    staged.path.write_bytes(b"new")
    with pytest.raises(FileExistsError):
        storage.place(staged, UUID(ctx["me"]["clinic_id"]), "a" * 32)
    assert files[0].read_bytes() == png(2, 2)


async def test_idempotent_retry_returns_one_document_and_mismatch_conflicts(env):
    client, sessions, store = env
    ctx = await clinic(client, "t11-retry@example.com")
    pid = await patient(client, ctx["auth"])
    key = str(uuid4())
    first = await upload(client, ctx["auth"], pid, png(), key=key)
    retry = await upload(client, ctx["auth"], pid, png(), key=key)
    assert first.status_code == 201 and retry.status_code in (200, 201)
    assert retry.json()["id"] == first.json()["id"]
    other = await upload(client, ctx["auth"], pid, png(9, 9), key=key)
    assert other.status_code == 409
    assert await doc_count(sessions) == 1 and len(stored_files(store)) == 1


async def test_archive_keeps_metadata_and_file_and_creates_no_billing_or_timer_records(env):
    client, sessions, store = env
    ctx = await clinic(client, "t11-archive@example.com")
    pid = await patient(client, ctx["auth"])
    vid = await visit(client, ctx, pid)
    doc = (await upload(client, ctx["auth"], pid, png(), appointment_id=vid, tooth_fdi="21")).json()
    assert (await client.post(f"/api/documents/{doc['id']}/archive", headers=ctx["auth"], json={"reason": " "})).status_code == 422
    archived = await client.post(f"/api/documents/{doc['id']}/archive", headers=ctx["auth"], json={"reason": "Încărcat greșit"})
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived" and archived.json()["archive_reason"] == "Încărcat greșit"
    assert (await client.post(f"/api/documents/{doc['id']}/archive", headers=ctx["auth"], json={"reason": "din nou"})).status_code == 409
    assert (await client.get(f"/api/documents/patients/{pid}", headers=ctx["auth"])).json() == []
    with_archived = (await client.get(f"/api/documents/patients/{pid}", params={"include_archived": "true"}, headers=ctx["auth"])).json()
    assert [row["id"] for row in with_archived] == [doc["id"]] and with_archived[0]["tooth_fdi"] == "21"
    assert (await client.get(f"/api/documents/{doc['id']}/content", headers=ctx["auth"])).status_code == 200
    assert len(stored_files(store)) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PerformedProcedure)) == 0
        assert await session.scalar(select(func.count()).select_from(ProcedureExecutionSegment)) == 0


async def test_storage_must_be_configured_and_outside_the_repository(env, monkeypatch):
    client, sessions, _ = env
    ctx = await clinic(client, "t11-config@example.com")
    pid = await patient(client, ctx["auth"])
    monkeypatch.setattr(settings, "document_storage_dir", None)
    assert (await upload(client, ctx["auth"], pid, png())).status_code == 503
    monkeypatch.setattr(settings, "document_storage_dir", str(storage.REPO_ROOT / "frontend" / "public" / "docs"))
    assert (await upload(client, ctx["auth"], pid, png())).status_code == 503
    monkeypatch.setattr(settings, "document_storage_dir", "relative/dir")
    assert (await upload(client, ctx["auth"], pid, png())).status_code == 503
    assert not (storage.REPO_ROOT / "frontend" / "public" / "docs").exists()
    assert await doc_count(sessions) == 0


async def test_missing_file_on_disk_is_reported_without_internal_paths(env):
    client, _sessions, store = env
    ctx = await clinic(client, "t11-missing@example.com")
    pid = await patient(client, ctx["auth"])
    doc = (await upload(client, ctx["auth"], pid, png())).json()
    for path in stored_files(store):
        os.remove(path)
    response = await client.get(f"/api/documents/{doc['id']}/content", headers=ctx["auth"])
    assert response.status_code == 404 and str(store) not in response.text

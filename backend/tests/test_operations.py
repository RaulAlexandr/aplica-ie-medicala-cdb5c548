import hashlib
import json
import tarfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main as main_module
from app.database import (
    AccessLog,
    Base,
    Clinic,
    ClinicalNote,
    LoginAttempt,
    Patient,
    PatientFinancialEntry,
    User,
    get_db,
    utcnow,
)
from app.main import app
from app.operations.backup import (
    OperationError,
    require_empty_database,
    verify_archive,
    verify_compatibility,
)
from app.operations.readiness import check_readiness
from app.operations.retention import cleanup_security_history


@pytest.fixture
async def ops_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()


@pytest.mark.asyncio
async def test_readiness_checks_database_and_document_read_write(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ready = await check_readiness(sessions, tmp_path)
    assert ready == {"database": "ok", "documents": "ok"}
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("fictiv")
    failed = await check_readiness(sessions, blocked)
    assert failed["database"] == "ok" and failed["documents"] == "unavailable"
    await engine.dispose()


@pytest.mark.asyncio
async def test_readiness_reports_database_unavailable_without_details(tmp_path):
    class BrokenFactory:
        def __call__(self):
            raise OSError("database password should not be returned")

    result = await check_readiness(BrokenFactory(), tmp_path)
    assert result == {"database": "unavailable", "documents": "ok"}


@pytest.mark.asyncio
async def test_readiness_route_returns_200_and_sanitized_503(ops_client, tmp_path, monkeypatch):
    client, sessions = ops_client
    monkeypatch.setattr(main_module, "SessionLocal", sessions)
    monkeypatch.setattr(main_module.settings, "document_storage_dir", str(tmp_path))
    ready = await client.get("/health/ready")
    assert ready.status_code == 200 and ready.json() == {
        "status": "ready", "checks": {"database": "ok", "documents": "ok"}
    }
    blocked = tmp_path / "blocked"
    blocked.write_text("fictiv")
    monkeypatch.setattr(main_module.settings, "document_storage_dir", str(blocked))
    unavailable = await client.get("/health/ready")
    assert unavailable.status_code == 503
    assert unavailable.json()["checks"] == {"database": "ok", "documents": "unavailable"}
    assert "password" not in unavailable.text and str(blocked) not in unavailable.text


@pytest.mark.asyncio
async def test_request_id_is_propagated_and_logs_exclude_secrets(ops_client, caplog):
    client, _ = ops_client
    response = await client.get("/health", headers={"X-Request-ID": "ops-test-123", "Authorization": "Bearer hidden-token"}, params={"token": "query-secret"})
    assert response.status_code == 200 and response.headers["x-request-id"] == "ops-test-123"
    output = " ".join(record.getMessage() for record in caplog.records)
    assert any(getattr(record, "request_id", None) == "ops-test-123" for record in caplog.records)
    assert "hidden-token" not in output and "query-secret" not in output


@pytest.mark.asyncio
async def test_generated_request_id_and_generic_500(ops_client, caplog):
    client, _ = ops_client

    async def fail():
        raise RuntimeError("password=secret-value Authorization: Bearer token-value")

    app.add_api_route("/_test/operations-error", fail, methods=["GET"])
    try:
        response = await client.get("/_test/operations-error")
    finally:
        app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != "/_test/operations-error"]
    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert request_id
    assert response.json() == {"detail": "Eroare internă.", "request_id": request_id}
    assert "secret-value" not in response.text and "token-value" not in response.text
    failure = next(record for record in caplog.records if getattr(record, "event_name", None) == "request.exception")
    assert failure.request_id == request_id and failure.stack
    assert "secret-value" not in failure.getMessage() and "token-value" not in failure.getMessage()


def write_archive(path, *, stored_payload=b"pg dump", manifest_payload=b"document bytes", heads=("t34b_operations",)):
    files = {"database.dump": hashlib.sha256(stored_payload).hexdigest(),
             "documents/fictiv.txt": hashlib.sha256(manifest_payload).hexdigest()}
    manifest = {"format_version": 1, "app_version": "0.1.0", "alembic_heads": list(heads),
                "table_counts": {"patients": 1}, "files": files}
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in (("database.dump", stored_payload), ("documents/fictiv.txt", manifest_payload),
                              ("manifest.json", manifest_bytes),
                              ("manifest.sha256", hashlib.sha256(manifest_bytes).hexdigest().encode())):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, BytesIO(payload))


def test_backup_manifest_verifies_files_and_heads(tmp_path):
    archive = tmp_path / "valid.tar.gz"
    write_archive(archive)
    manifest = verify_archive(archive, tmp_path / "extract", expected_heads=["t34b_operations"])
    assert manifest["table_counts"]["patients"] == 1
    with pytest.raises(OperationError, match="Alembic"):
        verify_compatibility(["different_head"], ["t34b_operations"])
    require_empty_database("0")
    with pytest.raises(OperationError, match="nu este goală"):
        require_empty_database("2")


def test_corrupt_archive_manifest_refused(tmp_path):
    archive = tmp_path / "corrupt.tar.gz"
    write_archive(archive)
    with tarfile.open(archive, "r:gz") as source:
        entries = [(item.name, source.extractfile(item).read()) for item in source.getmembers()]
    with tarfile.open(archive, "w:gz") as changed:
        for name, payload in entries:
            if name == "database.dump":
                payload = b"changed dump"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            changed.addfile(info, BytesIO(payload))
    with pytest.raises(OperationError, match="hash"):
        verify_archive(archive, tmp_path / "extract", expected_heads=["t34b_operations"])


@pytest.mark.asyncio
async def test_retention_dry_run_then_deletes_only_security_history(ops_client):
    _, sessions = ops_client
    old = utcnow() - timedelta(days=400)
    async with sessions() as db:
        clinic = Clinic(name="Clinică fictivă")
        db.add(clinic)
        await db.flush()
        user = User(clinic_id=clinic.id, full_name="Doctor fictiv", email="doctor@example.test",
                    password_hash="not-a-real-hash", role="doctor")
        db.add(user)
        await db.flush()
        patient = Patient(clinic_id=clinic.id, first_name="Ana", last_name="Test")
        db.add(patient)
        await db.flush()
        db.add_all([
            LoginAttempt(email="failed@example.test", attempted_at=old, succeeded=False),
            AccessLog(clinic_id=clinic.id, actor_id=user.id, patient_id=patient.id, action="patient.read", occurred_at=old),
            ClinicalNote(clinic_id=clinic.id, patient_id=patient.id, author_id=user.id, content="notă fictivă", created_at=old),
            PatientFinancialEntry(clinic_id=clinic.id, patient_id=patient.id, kind="payment", amount=Decimal("10.00"),
                method="cash", client_request_id=str(uuid4()), created_by_id=user.id, created_at=old),
        ])
        await db.commit()
        dry = await cleanup_security_history(db, now=utcnow(), login_days=90, access_days=365, dry_run=True)
        assert dry["dry_run"] is True and dry["counts"] == {"login_attempts": 1, "patient_access_log": 1}
        assert await db.scalar(select(LoginAttempt.id)) is not None
        applied = await cleanup_security_history(db, now=utcnow(), login_days=90, access_days=365, dry_run=False)
        assert applied["deleted"] == {"login_attempts": 1, "patient_access_log": 1}
        assert await db.scalar(select(LoginAttempt.id)) is None
        assert await db.scalar(select(AccessLog.id)) is None
        assert await db.scalar(select(ClinicalNote.id)) is not None
        assert await db.scalar(select(PatientFinancialEntry.id)) is not None

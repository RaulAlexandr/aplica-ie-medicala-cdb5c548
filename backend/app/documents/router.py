"""T11 clinical documents and images in the patient record.

Upload is a raw request body (``application/octet-stream``) so the size limit is enforced while
streaming, before anything is accepted. Metadata travels in query parameters (references) and
percent-encoded headers (free text). Content is returned only through the authorized
``/content`` route; there is no static mount, signed link or token in a URL.

Roles: clinic_manager, doctor, administrator. Reception and assistants receive 403 on every T11
route in the pilot. Other clinics' documents are 404.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import quote, unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.config import settings
from app.database import Appointment, AuditEvent, ClinicalDocument, Patient, User, get_db, utcnow
from app.documents import storage
from app.performed_procedures.router import validate_fdi_tooth

router = APIRouter(prefix="/documents", tags=["documents"])
DOCUMENT_ROLES = ("clinic_manager", "doctor", "administrator")
CATEGORIES = ("photo", "radiograph", "document", "other")
IMAGE_ONLY_CATEGORIES = {"photo", "radiograph"}
MAX_DESCRIPTION = 500
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "same-site",
    "Referrer-Policy": "no-referrer",
}


class ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Reason must not be blank")
        return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat().replace("+00:00", "Z")


def _decode_header(raw: str | None, name: str, limit: int) -> str | None:
    if raw is None:
        return None
    try:
        value = unquote(raw, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, f"{name} must be percent-encoded UTF-8") from exc
    value = value.strip()
    if len(value) > limit:
        raise HTTPException(422, f"{name} is too long")
    return value or None


def _uuid_key(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(UUID(raw))
    except ValueError as exc:
        raise HTTPException(422, "Idempotency-Key must be a UUID") from exc


def _limits() -> dict:
    return {
        "accepted_types": ["image/jpeg", "image/png", "application/pdf"],
        "accepted_extensions": [".jpg", ".jpeg", ".png", ".pdf"],
        "max_bytes": settings.document_max_bytes,
        "max_image_side": settings.document_max_image_side,
        "max_image_pixels": settings.document_max_image_pixels,
        "categories": list(CATEGORIES),
        "image_only_categories": sorted(IMAGE_ONLY_CATEGORIES),
        "not_supported": ["DICOM", "SVG", "HTML", "PDF with JavaScript/embedded files"],
        "antivirus_scan": False,
    }


async def _patient(db: AsyncSession, patient_id: UUID, user: User) -> Patient:
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found or access denied")
    return patient


async def _validate_refs(db: AsyncSession, clinic_id: UUID, patient_id: UUID, appointment_id: UUID | None) -> None:
    patient = await db.scalar(select(Patient.id).where(Patient.id == patient_id, Patient.clinic_id == clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found or access denied")
    if appointment_id is not None:
        appointment = await db.scalar(select(Appointment).where(
            Appointment.id == appointment_id, Appointment.clinic_id == clinic_id))
        if not appointment or appointment.patient_id != patient_id:
            raise HTTPException(422, "Visit does not belong to this patient")


async def _document(db: AsyncSession, document_id: UUID, user: User, *, lock: bool = False) -> ClinicalDocument:
    query = select(ClinicalDocument).where(
        ClinicalDocument.id == document_id, ClinicalDocument.clinic_id == user.clinic_id)
    if lock:
        query = query.with_for_update()
    document = await db.scalar(query)
    if not document:
        raise HTTPException(404, "Document not found or access denied")
    return document


async def _names(db: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str]:
    ids = {value for value in ids if value}
    if not ids:
        return {}
    return {row.id: row.full_name for row in await db.scalars(select(User).where(User.id.in_(ids)))}


async def _serialize(db: AsyncSession, documents: list[ClinicalDocument]) -> list[dict]:
    names = await _names(db, {d.uploaded_by_id for d in documents} | {d.archived_by_id for d in documents})
    appointment_ids = {d.appointment_id for d in documents if d.appointment_id}
    appointments = {}
    if appointment_ids:
        appointments = {row.id: row for row in await db.scalars(select(Appointment).where(
            Appointment.id.in_(appointment_ids)))}
    result = []
    for d in documents:
        appointment = appointments.get(d.appointment_id)
        result.append({
            "id": str(d.id), "patient_id": str(d.patient_id), "category": d.category,
            "description": d.description, "original_filename": d.original_filename,
            "content_type": d.content_type, "size_bytes": d.size_bytes, "width": d.width, "height": d.height,
            "is_image": d.content_type in storage.IMAGE_TYPES, "tooth_fdi": d.tooth_fdi,
            "appointment": None if not appointment else {
                "id": str(appointment.id), "starts_at": _iso(appointment.starts_at),
                "appointment_type": appointment.appointment_type, "status": appointment.status},
            "clinical_date": d.clinical_date.isoformat() if d.clinical_date else None,
            "uploaded_at": _iso(d.uploaded_at), "uploaded_by": {"id": str(d.uploaded_by_id), "name": names.get(d.uploaded_by_id, "—")},
            "status": d.status, "archived_at": _iso(d.archived_at),
            "archived_by": names.get(d.archived_by_id) if d.archived_by_id else None,
            "archive_reason": d.archive_reason, "sha256": d.sha256,
        })
    return result


def _audit(db: AsyncSession, document: ClinicalDocument, actor_id: UUID, action: str) -> None:
    db.add(AuditEvent(clinic_id=document.clinic_id, actor_id=actor_id, entity_type="clinical_document",
                      entity_id=document.id, action=action))


@router.get("/limits")
async def limits(user: User = Depends(require_roles(*DOCUMENT_ROLES))) -> dict:
    return _limits()


@router.get("/patients/{patient_id}")
async def list_documents(
    patient_id: UUID,
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCUMENT_ROLES)),
) -> list[dict]:
    await _patient(db, patient_id, user)
    query = select(ClinicalDocument).where(
        ClinicalDocument.clinic_id == user.clinic_id, ClinicalDocument.patient_id == patient_id)
    if not include_archived:
        query = query.where(ClinicalDocument.status == "active")
    documents = list(await db.scalars(query.order_by(ClinicalDocument.uploaded_at.desc(), ClinicalDocument.id)))
    return await _serialize(db, documents)


@router.post("/patients/{patient_id}", status_code=status.HTTP_201_CREATED)
async def upload_document(
    patient_id: UUID,
    request: Request,
    category: Literal["photo", "radiograph", "document", "other"] = Query(...),
    appointment_id: UUID | None = Query(default=None),
    tooth_fdi: str | None = Query(default=None, max_length=3),
    clinical_date: date | None = Query(default=None),
    file_name: str | None = Header(default=None, alias="X-File-Name"),
    description_header: str | None = Header(default=None, alias="X-Document-Description"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    content_length: int | None = Header(default=None, alias="Content-Length"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCUMENT_ROLES)),
):
    try:
        tooth = validate_fdi_tooth(tooth_fdi)
    except ValueError as exc:
        raise HTTPException(422, "Invalid FDI tooth code (11-18, 21-28, 31-38, 41-48)") from exc
    if clinical_date is not None and clinical_date > (utcnow() + timedelta(days=1)).date():
        raise HTTPException(422, "Clinical date cannot be in the future")
    display_name = storage.safe_display_name(_decode_header(file_name, "X-File-Name", 1024))
    description = _decode_header(description_header, "X-Document-Description", MAX_DESCRIPTION)
    key = _uuid_key(idempotency_key)
    max_bytes = settings.document_max_bytes
    if content_length is not None and content_length > max_bytes:
        raise HTTPException(413, f"File exceeds the {max_bytes // (1024 * 1024)} MiB limit")

    # The session is rolled back below (expiring ORM objects): keep plain identifiers.
    clinic_id, user_id = user.clinic_id, user.id
    # Cheap pre-check before accepting the body; then release the DB connection while streaming.
    await _validate_refs(db, clinic_id, patient_id, appointment_id)
    await db.rollback()

    staged = None
    final_path = None
    try:
        try:
            staged = await storage.stage_upload(request.stream(), max_bytes)
            storage.inspect_content(staged)
            storage.check_extension(display_name, staged.content_type)
        except storage.ContentRejected as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        if category in IMAGE_ONLY_CATEGORIES and staged.content_type not in storage.IMAGE_TYPES:
            raise HTTPException(422, "Photos and radiographs must be JPEG or PNG images")

        if key:
            existing = await db.scalar(select(ClinicalDocument).where(
                ClinicalDocument.clinic_id == clinic_id, ClinicalDocument.uploaded_by_id == user_id,
                ClinicalDocument.idempotency_key == key))
            if existing:
                if existing.patient_id != patient_id or existing.sha256 != staged.sha256:
                    raise HTTPException(409, "Idempotency-Key was already used with a different upload")
                return (await _serialize(db, [existing]))[0]

        # Authoritative validation in the same transaction as the insert.
        await _validate_refs(db, clinic_id, patient_id, appointment_id)
        storage_key = storage.new_storage_key()
        document = ClinicalDocument(
            clinic_id=clinic_id, patient_id=patient_id, appointment_id=appointment_id, tooth_fdi=tooth,
            category=category, description=description, original_filename=display_name,
            content_type=staged.content_type, size_bytes=staged.size, sha256=staged.sha256,
            width=staged.width, height=staged.height, storage_key=storage_key, clinical_date=clinical_date,
            uploaded_by_id=user_id, uploaded_at=utcnow(), idempotency_key=key, status="active")
        db.add(document)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, "Document could not be saved; retry the upload") from exc
        _audit(db, document, user_id, "uploaded")
        try:
            final_path = storage.place(staged, clinic_id, storage_key)
        except OSError as exc:
            raise storage.StorageUnavailable("Document could not be stored") from exc
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            storage.discard(final_path)
            final_path = None
            if key:
                existing = await db.scalar(select(ClinicalDocument).where(
                    ClinicalDocument.clinic_id == clinic_id, ClinicalDocument.uploaded_by_id == user_id,
                    ClinicalDocument.idempotency_key == key))
                if existing and existing.patient_id == patient_id and existing.sha256 == staged.sha256:
                    return (await _serialize(db, [existing]))[0]
            raise HTTPException(409, "Document could not be saved") from exc
        final_path = None  # committed: the file now belongs to a visible document
        return (await _serialize(db, [document]))[0]
    except storage.StorageUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        if final_path is not None:
            # Anything after placing the file failed before commit: no visible row, remove the file.
            await db.rollback()
            storage.discard(final_path)
        if staged is not None:
            storage.discard(staged.path)


@router.get("/{document_id}")
async def get_document(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCUMENT_ROLES)),
) -> dict:
    return (await _serialize(db, [await _document(db, document_id, user)]))[0]


@router.get("/{document_id}/content")
async def document_content(
    document_id: UUID,
    download: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCUMENT_ROLES)),
):
    document = await _document(db, document_id, user)
    try:
        path = storage.content_path(document.clinic_id, document.storage_key)
    except storage.StorageUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, "Document content unavailable")
    _audit(db, document, user.id, "downloaded" if download else "viewed")
    await db.commit()
    # PDFs are always an attachment: the app does not render PDFs inline.
    disposition = "attachment" if download or document.content_type == "application/pdf" else "inline"
    ascii_name = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._- ") else "_" for ch in document.original_filename)
    headers = {
        **SECURITY_HEADERS,
        "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(document.original_filename)}",
    }
    return FileResponse(path, media_type=document.content_type, headers=headers)


@router.post("/{document_id}/archive")
async def archive_document(
    document_id: UUID,
    payload: ArchiveRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCUMENT_ROLES)),
) -> dict:
    document = await _document(db, document_id, user, lock=True)
    if document.status == "archived":
        raise HTTPException(409, "Document is already archived")
    document.status = "archived"
    document.archived_at = utcnow()
    document.archived_by_id = user.id
    document.archive_reason = payload.reason
    _audit(db, document, user.id, "archived")
    await db.commit()
    await db.refresh(document)
    return (await _serialize(db, [document]))[0]

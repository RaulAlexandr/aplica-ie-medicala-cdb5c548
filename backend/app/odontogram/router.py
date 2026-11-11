from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import (
    AuditEvent,
    OdontogramFinding,
    OdontogramFindingRevision,
    OdontogramToothRevision,
    OdontogramToothState,
    Patient,
    User,
    get_db,
    utcnow,
)

router = APIRouter(prefix="/odontogram", tags=["odontogram"])
CLINICAL_ROLES = ("clinic_manager", "doctor", "administrator")
MANAGERS = ("clinic_manager", "administrator")
FDI_TEETH = tuple(
    f"{quadrant}{position}" for quadrant in "1234" for position in "12345678"
)
SURFACES = {
    "mesial", "distal", "buccal", "lingual", "palatal", "occlusal",
    "incisal", "vestibular", "labial", "approximal", "interproximal",
}
TOOTH_STATES = {"present", "missing", "unevaluated"}


class ToothStateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in TOOTH_STATES:
            raise ValueError("Invalid tooth status")
        return value


class FindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    toothId: str = Field(alias="toothId")
    kind: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=5000)
    surfaces: list[str] = Field(default_factory=list, max_length=11)
    observedAt: datetime | None = None
    status: str = "draft"

    @field_validator("kind", "description")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value must not be blank")
        return value

    @field_validator("toothId")
    @classmethod
    def valid_tooth(cls, value: str) -> str:
        if value not in FDI_TEETH:
            raise ValueError("Invalid FDI tooth code")
        return value

    @field_validator("surfaces")
    @classmethod
    def valid_surfaces(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(value not in SURFACES for value in values):
            raise ValueError("Invalid or duplicate tooth surface")
        return values

    @field_validator("status")
    @classmethod
    def draft_only(cls, value: str) -> str:
        if value != "draft":
            raise ValueError("Findings must be created as drafts")
        return value


class FindingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    surfaces: list[str] | None = Field(default=None, max_length=11)

    @field_validator("kind", "description")
    @classmethod
    def nonblank_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Value must not be blank")
        return value

    @field_validator("surfaces")
    @classmethod
    def valid_surfaces(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and (len(values) != len(set(values)) or any(v not in SURFACES for v in values)):
            raise ValueError("Invalid or duplicate tooth surface")
        return values


class FindingCorrection(FindingPatch):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
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


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _idempotent_finding_matches(
    finding: OdontogramFinding, patient_id: UUID, payload: FindingCreate
) -> bool:
    if finding.patient_id != patient_id or (
        finding.tooth_fdi, finding.kind, finding.description, finding.surfaces
    ) != (payload.toothId, payload.kind, payload.description, payload.surfaces):
        return False
    # Omitted observedAt means “reuse the original server-assigned time”.
    # Explicit values are compared as instants in UTC, independent of offset.
    return payload.observedAt is None or _utc(finding.observed_at) == _utc(payload.observedAt)


async def _patient(db: AsyncSession, patient_id: UUID, user: User, *, lock: bool = False) -> Patient:
    query = select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id)
    if lock:
        query = query.with_for_update()
    patient = await db.scalar(query)
    if not patient:
        raise HTTPException(404, "Patient not found or access denied")
    return patient


async def _actor(db: AsyncSession, actor_id: UUID) -> dict:
    user = await db.get(User, actor_id)
    return {"id": str(actor_id), "name": user.full_name if user else "Unknown"}


async def _finding(db: AsyncSession, finding_id: UUID, user: User, *, lock: bool = False) -> OdontogramFinding:
    query = select(OdontogramFinding).where(
        OdontogramFinding.id == finding_id,
        OdontogramFinding.clinic_id == user.clinic_id,
    )
    if lock:
        query = query.with_for_update()
    finding = await db.scalar(query)
    if not finding:
        raise HTTPException(404, "Finding not found or access denied")
    if finding.status == "draft" and finding.author_id != user.id:
        raise HTTPException(404, "Finding not found or access denied")
    return finding


async def _finding_response(db: AsyncSession, finding: OdontogramFinding) -> dict:
    author = await _actor(db, finding.author_id)
    revisions = await db.scalars(
        select(OdontogramFindingRevision)
        .where(OdontogramFindingRevision.finding_id == finding.id,
               OdontogramFindingRevision.clinic_id == finding.clinic_id)
        .order_by(OdontogramFindingRevision.occurred_at, OdontogramFindingRevision.id)
    )
    revision_items = []
    for revision in revisions:
        revision_items.append({
            "id": str(revision.id),
            "previousValue": revision.previous_value,
            "currentValue": revision.current_value,
            "reason": revision.reason,
            "occurredAt": _iso(revision.occurred_at),
            "actorId": str(revision.actor_id),
            "author": (await _actor(db, revision.actor_id))["name"],
        })
    return {
        "id": str(finding.id), "toothId": finding.tooth_fdi, "kind": finding.kind,
        "description": finding.description, "surfaces": finding.surfaces,
        "status": finding.status, "observedAt": _iso(finding.observed_at),
        "createdAt": _iso(finding.created_at), "updatedAt": _iso(finding.updated_at),
        "finalizedAt": _iso(finding.finalized_at), "authorId": author["id"],
        "author": author["name"],
        "revisions": revision_items,
    }


@router.get("/patients/{patient_id}")
async def get_odontogram(
    patient_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*CLINICAL_ROLES)),
) -> dict:
    await _patient(db, patient_id, user)
    tooth_rows = await db.scalars(select(OdontogramToothState).where(
        OdontogramToothState.clinic_id == user.clinic_id,
        OdontogramToothState.patient_id == patient_id,
    ))
    by_tooth = {row.tooth_fdi: row for row in tooth_rows}
    teeth = []
    for code in FDI_TEETH:
        row = by_tooth.get(code)
        if row:
            teeth.append({"toothId": code, "status": row.status, "updatedAt": _iso(row.updated_at), "updatedBy": await _actor(db, row.updated_by_id)})
        else:
            teeth.append({"toothId": code, "status": "unevaluated", "updatedAt": None, "updatedBy": None})
    findings = await db.scalars(
        select(OdontogramFinding).where(
            OdontogramFinding.clinic_id == user.clinic_id,
            OdontogramFinding.patient_id == patient_id,
            ((OdontogramFinding.status != "draft") | (OdontogramFinding.author_id == user.id)),
        ).order_by(OdontogramFinding.created_at, OdontogramFinding.id)
    )
    finding_items = [await _finding_response(db, finding) for finding in findings]
    history = []
    state_revisions = await db.scalars(select(OdontogramToothRevision).where(
        OdontogramToothRevision.clinic_id == user.clinic_id,
        OdontogramToothRevision.patient_id == patient_id,
    ).order_by(OdontogramToothRevision.occurred_at, OdontogramToothRevision.id))
    for revision in state_revisions:
        history.append({
            "id": str(revision.id), "toothId": revision.tooth_fdi,
            "occurredAt": _iso(revision.occurred_at), "author": (await _actor(db, revision.actor_id))["name"],
            "kind": "status_changed",
            "summary": f"Stare modificată: {revision.previous_status} → {revision.current_status}",
            "previousValue": revision.previous_status, "currentValue": revision.current_status,
        })
    for item in finding_items:
        history.append({
            "id": f"finding-{item['id']}", "toothId": item["toothId"],
            "occurredAt": item["createdAt"], "author": item["author"],
            "kind": "finding_added", "summary": f"Constatare adăugată: {item['kind']}",
        })
        for revision in item["revisions"]:
            history.append({
                "id": revision["id"], "toothId": item["toothId"],
                "occurredAt": revision["occurredAt"], "author": revision["author"],
                "kind": "finding_corrected", "summary": "Constatare corectată",
                "previousValue": f"{revision['previousValue']['kind']}: {revision['previousValue']['description']}",
                "currentValue": f"{revision['currentValue']['kind']}: {revision['currentValue']['description']}",
            })
    history.sort(key=lambda event: (event["occurredAt"] or "", event["id"]), reverse=True)
    return {"patientId": str(patient_id), "teeth": teeth, "findings": finding_items, "history": history}


@router.put("/patients/{patient_id}/teeth/{tooth_id}")
async def update_tooth_state(
    patient_id: UUID,
    tooth_id: str,
    payload: ToothStateInput,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*CLINICAL_ROLES)),
) -> dict:
    if tooth_id not in FDI_TEETH:
        raise HTTPException(422, "Invalid FDI tooth code")
    await _patient(db, patient_id, user, lock=True)
    row = await db.scalar(select(OdontogramToothState).where(
        OdontogramToothState.clinic_id == user.clinic_id,
        OdontogramToothState.patient_id == patient_id,
        OdontogramToothState.tooth_fdi == tooth_id,
    ).with_for_update())
    old = row.status if row else "unevaluated"
    now = utcnow()
    if old != payload.status:
        if row is None:
            row = OdontogramToothState(clinic_id=user.clinic_id, patient_id=patient_id, tooth_fdi=tooth_id, status=payload.status, updated_by_id=user.id, created_at=now, updated_at=now)
            db.add(row)
        else:
            row.status = payload.status
            row.updated_by_id = user.id
            row.updated_at = now
        db.add(OdontogramToothRevision(clinic_id=user.clinic_id, patient_id=patient_id, tooth_fdi=tooth_id, actor_id=user.id, previous_status=old, current_status=payload.status, occurred_at=now))
        await db.flush()
        db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="odontogram_tooth", entity_id=row.id, action="status_changed"))
        await db.commit()
        await db.refresh(row)
    return {"toothId": tooth_id, "status": row.status if row else "unevaluated",
            "previousStatus": old, "updatedAt": _iso(row.updated_at if row else None),
            "updatedBy": await _actor(db, row.updated_by_id) if row else None}


@router.post("/patients/{patient_id}/findings", status_code=status.HTTP_201_CREATED)
async def create_finding(
    patient_id: UUID,
    payload: FindingCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*CLINICAL_ROLES)),
) -> dict:
    await _patient(db, patient_id, user)
    if idempotency_key:
        try:
            idempotency_key = str(UUID(idempotency_key))
        except ValueError as exc:
            raise HTTPException(422, "Idempotency-Key must be a UUID") from exc
    if idempotency_key:
        existing = await db.scalar(select(OdontogramFinding).where(
            OdontogramFinding.clinic_id == user.clinic_id,
            OdontogramFinding.author_id == user.id,
            OdontogramFinding.idempotency_key == idempotency_key,
        ))
        if existing:
            if not _idempotent_finding_matches(existing, patient_id, payload):
                raise HTTPException(409, "Idempotency-Key was already used with a different request")
            return await _finding_response(db, existing)
    now = utcnow()
    observed = _utc(payload.observedAt) if payload.observedAt is not None else now
    finding = OdontogramFinding(
        clinic_id=user.clinic_id, patient_id=patient_id, tooth_fdi=payload.toothId,
        kind=payload.kind.strip(), description=payload.description.strip(),
        surfaces=payload.surfaces, status="draft", observed_at=observed,
        created_at=now, updated_at=now, author_id=user.id,
        idempotency_key=idempotency_key,
    )
    db.add(finding)
    try:
        await db.flush()
        db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="odontogram_finding", entity_id=finding.id, action="created"))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if idempotency_key:
            existing = await db.scalar(select(OdontogramFinding).where(
                OdontogramFinding.clinic_id == user.clinic_id,
                OdontogramFinding.author_id == user.id,
                OdontogramFinding.idempotency_key == idempotency_key,
            ))
            if existing and _idempotent_finding_matches(existing, patient_id, payload):
                return await _finding_response(db, existing)
        raise HTTPException(409, "Finding could not be saved")
    await db.refresh(finding)
    return await _finding_response(db, finding)


@router.patch("/findings/{finding_id}")
async def update_draft(
    finding_id: UUID,
    payload: FindingPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*CLINICAL_ROLES)),
) -> dict:
    finding = await _finding(db, finding_id, user, lock=True)
    if finding.status != "draft":
        raise HTTPException(409, "Finalized findings are immutable; create a correction")
    if finding.author_id != user.id:
        raise HTTPException(404, "Finding not found or access denied")
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        if field == "kind":
            value = value.strip()
        if field == "description":
            value = value.strip()
        setattr(finding, field, value)
    finding.updated_at = utcnow()
    await db.commit()
    await db.refresh(finding)
    return await _finding_response(db, finding)


@router.post("/findings/{finding_id}/finalize")
async def finalize_finding(
    finding_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*CLINICAL_ROLES)),
) -> dict:
    finding = await _finding(db, finding_id, user, lock=True)
    if finding.status == "finalized":
        raise HTTPException(409, "Finding is already finalized")
    if finding.author_id != user.id:
        raise HTTPException(404, "Finding not found or access denied")
    finding.status = "finalized"
    finding.finalized_at = finding.updated_at = utcnow()
    await db.commit()
    await db.refresh(finding)
    return await _finding_response(db, finding)


@router.post("/findings/{finding_id}/corrections")
async def correct_finding(
    finding_id: UUID,
    payload: FindingCorrection,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*CLINICAL_ROLES)),
) -> dict:
    finding = await _finding(db, finding_id, user, lock=True)
    if finding.status != "finalized":
        raise HTTPException(409, "Only finalized findings can be corrected")
    if finding.author_id != user.id and user.role not in MANAGERS:
        raise HTTPException(403, "Only the author or a manager can correct this finding")
    changes = payload.model_dump(exclude={"reason"}, exclude_unset=True, exclude_none=True)
    previous = {"kind": finding.kind, "description": finding.description, "surfaces": finding.surfaces}
    current = {**previous, **changes}
    current["kind"] = current["kind"].strip()
    current["description"] = current["description"].strip()
    if current == previous:
        raise HTTPException(422, "Correction must change at least one value")
    finding.kind, finding.description, finding.surfaces = current["kind"], current["description"], current["surfaces"]
    finding.updated_at = utcnow()
    db.add(OdontogramFindingRevision(
        clinic_id=user.clinic_id, finding_id=finding.id, actor_id=user.id,
        previous_value=previous, current_value=current, reason=payload.reason.strip(), occurred_at=finding.updated_at,
    ))
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="odontogram_finding", entity_id=finding.id, action="corrected"))
    await db.commit()
    await db.refresh(finding)
    return await _finding_response(db, finding)

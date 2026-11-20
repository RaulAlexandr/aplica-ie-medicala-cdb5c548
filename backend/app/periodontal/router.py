from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import (
    AuditEvent,
    Patient,
    PeriodontalAssessment,
    PeriodontalAssessmentTooth,
    PeriodontalSiteMeasurement,
    User,
    get_db,
    utcnow,
)

router = APIRouter(prefix="/periodontal", tags=["periodontal"])
CLINICAL_ROLES = ("clinic_manager", "doctor", "administrator")
FDI = {f"{q}{n}" for q in "1234" for n in range(1, 9)}
SITES = ("buccal_mesial", "buccal_mid", "buccal_distal", "oral_mesial", "oral_mid", "oral_distal")
MOBILITY = {"0", "1", "2", "3"}
FURCATION = {"I", "II", "III"}
# Product decision (PM, 2026-11-20): deeper than 20 mm is almost certainly a typing error.
MAX_PROBING_DEPTH_MM = 20


class SiteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str
    probing_depth_mm: Decimal | None = Field(default=None, max_digits=7, decimal_places=2, ge=0, le=MAX_PROBING_DEPTH_MM)
    recession_mm: Decimal | None = Field(default=None, max_digits=7, decimal_places=2)
    bop: StrictBool | None = None
    plaque: StrictBool | None = None
    suppuration: StrictBool | None = None

    @field_validator("probing_depth_mm", "recession_mm", mode="before")
    @classmethod
    def numeric_json_values(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float, Decimal))):
            raise ValueError("Measurement must be a JSON number or null")
        return value


class ToothInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tooth_fdi: str
    is_excluded: bool = False
    mobility: str | None = None
    furcation: str | None = None
    sites: list[SiteInput] = Field(min_length=6, max_length=6)

    @field_validator("tooth_fdi")
    @classmethod
    def valid_fdi(cls, value):
        if value not in FDI:
            raise ValueError("Use a permanent FDI tooth code (11–48)")
        return value

    @field_validator("mobility")
    @classmethod
    def valid_mobility(cls, value):
        if value is not None and value not in MOBILITY:
            raise ValueError("Mobility must be grade 0, 1, 2, or 3")
        return value

    @field_validator("furcation")
    @classmethod
    def valid_furcation(cls, value):
        if value is not None and value not in FURCATION:
            raise ValueError("Furcation must be grade I, II, or III")
        return value

    @model_validator(mode="after")
    def valid_sites(self):
        names = [site.site for site in self.sites]
        if set(names) != set(SITES):
            raise ValueError("Exactly the six canonical sites are required")
        if self.is_excluded and (self.mobility is not None or self.furcation is not None or any(
            value is not None for site in self.sites for value in (
                site.probing_depth_mm, site.recession_mm, site.bop, site.plaque, site.suppuration
            )
        )):
            raise ValueError("Excluded teeth cannot contain measurements")
        return self


class MeasurementsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teeth: list[ToothInput] = Field(max_length=32)

    @field_validator("teeth")
    @classmethod
    def unique_teeth(cls, values):
        names = [tooth.tooth_fdi for tooth in values]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate FDI tooth")
        return values


class AssessmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clinical_date: date
    previous_assessment_id: UUID | None = None
    relationship_kind: str | None = None
    change_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def linked_reason(self):
        if self.previous_assessment_id is None and (self.relationship_kind is not None or self.change_reason is not None):
            raise ValueError("Relationship details require a previous assessment")
        if self.previous_assessment_id is not None and self.relationship_kind not in {"follow_up", "correction"}:
            raise ValueError("Choose follow_up or correction")
        if self.relationship_kind == "correction" and not (self.change_reason or "").strip():
            raise ValueError("A correction requires a reason")
        return self


def _patient_query(patient_id: UUID, user: User):
    return select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id)

async def _get_assessment(db: AsyncSession, assessment_id: UUID, user: User, *, lock=False):
    query = select(PeriodontalAssessment).where(
        PeriodontalAssessment.id == assessment_id,
        PeriodontalAssessment.clinic_id == user.clinic_id,
    )
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query)
    if not row or (row.status == "draft" and row.author_id != user.id):
        raise HTTPException(404, "Assessment not found or access denied")
    return row

async def _author(db: AsyncSession, user_id: UUID):
    user = await db.get(User, user_id)
    return user.full_name if user else "Unknown"

async def _serialize(db: AsyncSession, assessment: PeriodontalAssessment):
    teeth = (await db.scalars(select(PeriodontalAssessmentTooth).where(
        PeriodontalAssessmentTooth.assessment_id == assessment.id
    ).order_by(PeriodontalAssessmentTooth.tooth_fdi))).all()
    items = []
    for tooth in teeth:
        sites = (await db.scalars(select(PeriodontalSiteMeasurement).where(
            PeriodontalSiteMeasurement.tooth_id == tooth.id
        ))).all()
        items.append({"tooth_fdi": tooth.tooth_fdi, "is_excluded": tooth.is_excluded,
            "mobility": tooth.mobility, "furcation": tooth.furcation,
            "sites": [{"site": site.site, "probing_depth_mm": site.probing_depth_mm,
                "recession_mm": site.recession_mm, "bop": site.bop, "plaque": site.plaque,
                "suppuration": site.suppuration} for site in sorted(sites, key=lambda s: SITES.index(s.site))]})
    return {"id": str(assessment.id), "clinic_id": str(assessment.clinic_id),
        "patient_id": str(assessment.patient_id), "author_id": str(assessment.author_id),
        "author_name": await _author(db, assessment.author_id), "clinical_date": assessment.clinical_date.isoformat(),
        "status": assessment.status, "previous_assessment_id": str(assessment.previous_assessment_id) if assessment.previous_assessment_id else None,
        "relationship_kind": assessment.relationship_kind, "change_reason": assessment.change_reason,
        "created_at": assessment.created_at.isoformat(), "updated_at": assessment.updated_at.isoformat(),
        "finalized_at": assessment.finalized_at.isoformat() if assessment.finalized_at else None, "teeth": items}

async def _replace_teeth(db, assessment, payload):
    existing = (await db.scalars(select(PeriodontalAssessmentTooth).where(
        PeriodontalAssessmentTooth.assessment_id == assessment.id
    ))).all()
    for item in existing:
        measurements = (await db.scalars(select(PeriodontalSiteMeasurement).where(
            PeriodontalSiteMeasurement.tooth_id == item.id
        ))).all()
        for measurement in measurements:
            await db.delete(measurement)
        await db.delete(item)
    await db.flush()
    for tooth_data in payload.teeth:
        tooth = PeriodontalAssessmentTooth(assessment_id=assessment.id, tooth_fdi=tooth_data.tooth_fdi,
            is_excluded=tooth_data.is_excluded, mobility=tooth_data.mobility, furcation=tooth_data.furcation)
        db.add(tooth)
        await db.flush()
        for site in tooth_data.sites:
            db.add(PeriodontalSiteMeasurement(tooth_id=tooth.id, **site.model_dump()))
    assessment.updated_at = utcnow()

@router.post("/patients/{patient_id}/assessments", status_code=201)
async def create_assessment(patient_id: UUID, payload: AssessmentCreate, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*CLINICAL_ROLES))):
    if not await db.scalar(_patient_query(patient_id, user)):
        raise HTTPException(404, "Patient not found or access denied")
    if payload.previous_assessment_id:
        previous = await _get_assessment(db, payload.previous_assessment_id, user)
        if previous.patient_id != patient_id or previous.status != "finalized":
            raise HTTPException(422, "Previous assessment must be finalized and belong to this patient")
    row = PeriodontalAssessment(clinic_id=user.clinic_id, patient_id=patient_id, author_id=user.id,
        clinical_date=payload.clinical_date, previous_assessment_id=payload.previous_assessment_id,
        relationship_kind=payload.relationship_kind, change_reason=payload.change_reason.strip() if payload.change_reason else None)
    db.add(row)
    await db.flush()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="periodontal_assessment",
        entity_id=row.id, action="created"))
    await db.commit()
    return await _serialize(db, row)

@router.get("/patients/{patient_id}/assessments")
async def list_assessments(patient_id: UUID, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*CLINICAL_ROLES))):
    if not await db.scalar(_patient_query(patient_id, user)):
        raise HTTPException(404, "Patient not found or access denied")
    query = select(PeriodontalAssessment).where(PeriodontalAssessment.clinic_id == user.clinic_id,
        PeriodontalAssessment.patient_id == patient_id)
    if user.role not in CLINICAL_ROLES:
        raise HTTPException(403, "Insufficient permissions")
    query = query.where((PeriodontalAssessment.status == "finalized") | (PeriodontalAssessment.author_id == user.id))
    rows = (await db.scalars(query.order_by(PeriodontalAssessment.clinical_date.desc(), PeriodontalAssessment.created_at.desc()))).all()
    return [{"id": str(r.id), "patient_id": str(r.patient_id), "author_id": str(r.author_id),
        "author_name": await _author(db, r.author_id), "clinical_date": r.clinical_date.isoformat(),
        "status": r.status, "previous_assessment_id": str(r.previous_assessment_id) if r.previous_assessment_id else None,
        "relationship_kind": r.relationship_kind, "change_reason": r.change_reason,
        "created_at": r.created_at.isoformat(), "finalized_at": r.finalized_at.isoformat() if r.finalized_at else None} for r in rows]

@router.get("/assessments/{assessment_id}")
async def get_assessment(assessment_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*CLINICAL_ROLES))):
    return await _serialize(db, await _get_assessment(db, assessment_id, user))

@router.put("/assessments/{assessment_id}/measurements")
async def save_measurements(assessment_id: UUID, payload: MeasurementsInput, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*CLINICAL_ROLES))):
    row = await _get_assessment(db, assessment_id, user, lock=True)
    if row.status != "draft":
        raise HTTPException(409, "Finalized assessments are immutable; create a new linked assessment")
    await _replace_teeth(db, row, payload)
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="periodontal_assessment",
        entity_id=row.id, action="measurements_saved"))
    await db.commit()
    return await _serialize(db, row)

@router.post("/assessments/{assessment_id}/finalize")
async def finalize_assessment(assessment_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*CLINICAL_ROLES))):
    row = await _get_assessment(db, assessment_id, user, lock=True)
    if row.status != "draft":
        raise HTTPException(409, "Assessment is already finalized")
    teeth = (await db.scalars(select(PeriodontalAssessmentTooth).where(PeriodontalAssessmentTooth.assessment_id == row.id))).all()
    if not teeth:
        raise HTTPException(422, "Enter measured data before finalizing")
    any_measured = False
    for tooth in teeth:
        if tooth.is_excluded:
            continue
        sites = (await db.scalars(select(PeriodontalSiteMeasurement).where(PeriodontalSiteMeasurement.tooth_id == tooth.id))).all()
        any_measured |= tooth.mobility is not None or tooth.furcation is not None or any(
            value is not None for site in sites for value in (
                site.probing_depth_mm, site.recession_mm, site.bop, site.plaque, site.suppuration
            ))
    if not any_measured:
        raise HTTPException(422, "Enter measured data before finalizing")
    row.status = "finalized"
    row.finalized_at = row.updated_at = utcnow()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="periodontal_assessment",
        entity_id=row.id, action="finalized"))
    await db.commit()
    return await _serialize(db, row)

@router.get("/patients/{patient_id}/compare")
async def compare_assessments(patient_id: UUID, first_id: UUID, second_id: UUID,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*CLINICAL_ROLES))):
    if first_id == second_id or not await db.scalar(_patient_query(patient_id, user)):
        raise HTTPException(404, "Patient or comparison not found")
    first, second = await _get_assessment(db, first_id, user), await _get_assessment(db, second_id, user)
    if first.patient_id != patient_id or second.patient_id != patient_id or first.status != "finalized" or second.status != "finalized":
        raise HTTPException(404, "Finalized assessments for this patient were not found")
    left, right = await _serialize(db, first), await _serialize(db, second)
    teeth = []
    left_teeth = {x["tooth_fdi"]: x for x in left["teeth"]}
    right_teeth = {x["tooth_fdi"]: x for x in right["teeth"]}
    fields = ("probing_depth_mm", "recession_mm", "bop", "plaque", "suppuration")
    for fdi in sorted(set(left_teeth) | set(right_teeth)):
        lt, rt = left_teeth.get(fdi), right_teeth.get(fdi)
        ls = {x["site"]: x for x in lt["sites"]} if lt and not lt["is_excluded"] else {}
        rs = {x["site"]: x for x in rt["sites"]} if rt and not rt["is_excluded"] else {}
        sites = []
        for name in SITES:
            a, b = ls.get(name), rs.get(name)
            measured_both = bool(a and b)
            row = {"site": name, "first": a, "second": b, "differences": {}}
            for field in fields:
                av, bv = (a or {}).get(field), (b or {}).get(field)
                row["differences"][field] = (float(Decimal(str(bv)) - Decimal(str(av)))
                    if measured_both and field in {"probing_depth_mm", "recession_mm"} and av is not None and bv is not None
                    else (bv != av if measured_both and field in {"bop", "plaque", "suppuration"} and av is not None and bv is not None else None))
            sites.append(row)
        known = [item["second"]["bop"] for item in sites if item["second"] and item["second"]["bop"] is not None]
        per_tooth = {}
        for field in ("mobility", "furcation"):
            av = (lt or {}).get(field) if lt and not lt["is_excluded"] else None
            bv = (rt or {}).get(field) if rt and not rt["is_excluded"] else None
            per_tooth[field] = {"first": av, "second": bv,
                "changed": (av != bv) if av is not None and bv is not None else None}
        teeth.append({"tooth_fdi": fdi, "mobility": per_tooth["mobility"], "furcation": per_tooth["furcation"],
            "sites": sites, "second_bop_positive": sum(known),
            "second_bop_known_denominator": len(known), "second_bop_percent": (sum(known) * 100 / len(known)) if known else None})
    return {"first": {k: left[k] for k in ("id", "clinical_date", "author_name")},
        "second": {k: right[k] for k in ("id", "clinical_date", "author_name")}, "teeth": teeth}

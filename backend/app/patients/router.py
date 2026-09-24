from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import AuditEvent, Patient, PatientRevision, User, get_db, utcnow

router = APIRouter(prefix="/patients", tags=["patients"])
PATIENT_FIELDS = ("first_name", "last_name", "date_of_birth", "sex", "phone", "email", "address", "emergency_contact", "occupation", "allergies", "medications", "chronic_diseases", "pregnancy_status", "smoking_status", "previous_surgeries", "relevant_medical_conditions", "medical_alerts", "notes")
CLINICAL_FIELDS = {"allergies", "medications", "chronic_diseases", "pregnancy_status", "smoking_status", "previous_surgeries", "relevant_medical_conditions", "medical_alerts", "notes"}

class PatientPayload(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    date_of_birth: date | None = None
    sex: str | None = Field(default=None, max_length=30)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    address: str | None = Field(default=None, max_length=300)
    emergency_contact: str | None = Field(default=None, max_length=160)
    occupation: str | None = Field(default=None, max_length=120)
    allergies: str | None = None
    medications: str | None = None
    chronic_diseases: str | None = None
    pregnancy_status: str | None = Field(default=None, max_length=40)
    smoking_status: str | None = Field(default=None, max_length=40)
    previous_surgeries: str | None = None
    relevant_medical_conditions: str | None = None
    medical_alerts: str | None = None
    notes: str | None = None

class PatientPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str = Field(default=None, min_length=1, max_length=80)
    last_name: str = Field(default=None, min_length=1, max_length=80)
    date_of_birth: date | None = None
    sex: str | None = Field(default=None, max_length=30)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    address: str | None = Field(default=None, max_length=300)
    emergency_contact: str | None = Field(default=None, max_length=160)
    occupation: str | None = Field(default=None, max_length=120)
    allergies: str | None = None
    medications: str | None = None
    chronic_diseases: str | None = None
    pregnancy_status: str | None = Field(default=None, max_length=40)
    smoking_status: str | None = Field(default=None, max_length=40)
    previous_surgeries: str | None = None
    relevant_medical_conditions: str | None = None
    medical_alerts: str | None = None
    notes: str | None = None

class PatientResponse(PatientPayload):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    clinic_id: UUID
    created_at: datetime
    updated_at: datetime
class PatientRevisionResponse(BaseModel):
    id: UUID
    actor_id: UUID
    field: str
    previous_value: str | None
    new_value: str | None
    occurred_at: datetime
    model_config = ConfigDict(from_attributes=True)

def response(patient: Patient) -> PatientResponse:
    return PatientResponse.model_validate(patient, from_attributes=True)

def _serialized(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)

@router.get("", response_model=list[PatientResponse])
async def list_patients(search: str | None = Query(default=None, max_length=100), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "assistant", "reception", "administrator"))):
    query = select(Patient).where(Patient.clinic_id == user.clinic_id).order_by(Patient.last_name, Patient.first_name).offset(offset).limit(limit)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(or_(Patient.first_name.ilike(term), Patient.last_name.ilike(term), Patient.phone.ilike(term), Patient.email.ilike(term)))
    return [response(item) for item in (await db.scalars(query)).all()]

@router.get("/count", response_model=int)
async def count_patients(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "assistant", "reception", "administrator"))):
    return int(await db.scalar(select(func.count(Patient.id)).where(Patient.clinic_id == user.clinic_id)) or 0)

@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
async def create_patient(payload: PatientPayload, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "assistant", "reception", "administrator"))):
    patient = Patient(clinic_id=user.clinic_id, **payload.model_dump())
    db.add(patient)
    await db.flush()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="patient", entity_id=patient.id, action="created"))
    await db.commit()
    await db.refresh(patient)
    return response(patient)

@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(patient_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "assistant", "reception", "administrator"))):
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found")
    return response(patient)

@router.patch("/{patient_id}", response_model=PatientResponse)
async def update_patient(patient_id: UUID, payload: PatientPatch, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "administrator"))):
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        previous = getattr(patient, key)
        if previous != value:
            if key in CLINICAL_FIELDS or key in {"first_name", "last_name", "date_of_birth", "phone", "email"}:
                db.add(PatientRevision(clinic_id=user.clinic_id, patient_id=patient.id, actor_id=user.id, field=key, previous_value=_serialized(previous), new_value=_serialized(value)))
            setattr(patient, key, value)
    patient.updated_at = utcnow()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="patient", entity_id=patient.id, action="updated"))
    await db.commit()
    await db.refresh(patient)
    return response(patient)

@router.get("/{patient_id}/history", response_model=list[PatientRevisionResponse])
async def patient_history(patient_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "administrator"))):
    patient = await db.scalar(select(Patient.id).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found")
    revisions = await db.scalars(select(PatientRevision).where(PatientRevision.patient_id == patient_id, PatientRevision.clinic_id == user.clinic_id).order_by(PatientRevision.occurred_at.desc()))
    return list(revisions)

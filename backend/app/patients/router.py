from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_current_user
from app.database import AuditEvent, Patient, User, get_db, utcnow

router = APIRouter(prefix="/patients", tags=["patients"])


class PatientPayload(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    date_of_birth: date | None = None
    sex: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    emergency_contact: str | None = None
    occupation: str | None = None
    allergies: str | None = None
    medications: str | None = None
    chronic_diseases: str | None = None
    pregnancy_status: str | None = None
    smoking_status: str | None = None
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


def response(patient: Patient) -> PatientResponse:
    return PatientResponse.model_validate(patient, from_attributes=True)


@router.get("", response_model=list[PatientResponse])
async def list_patients(search: str | None = Query(default=None, max_length=100), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    query = select(Patient).where(Patient.clinic_id == user.clinic_id).order_by(Patient.last_name, Patient.first_name).offset(offset).limit(limit)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(or_(Patient.first_name.ilike(term), Patient.last_name.ilike(term), Patient.phone.ilike(term), Patient.email.ilike(term)))
    return [response(item) for item in (await db.scalars(query)).all()]


@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
async def create_patient(payload: PatientPayload, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    patient = Patient(clinic_id=user.clinic_id, **payload.model_dump())
    db.add(patient)
    await db.flush()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="patient", entity_id=patient.id, action="created"))
    await db.commit()
    await db.refresh(patient)
    return response(patient)


@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(patient_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found")
    return response(patient)


@router.patch("/{patient_id}", response_model=PatientResponse)
async def update_patient(patient_id: UUID, payload: PatientPayload, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found")
    for key, value in payload.model_dump().items():
        setattr(patient, key, value)
    patient.updated_at = utcnow()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="patient", entity_id=patient.id, action="updated"))
    await db.commit()
    await db.refresh(patient)
    return response(patient)

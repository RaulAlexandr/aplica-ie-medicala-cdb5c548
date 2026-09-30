from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import AuditEvent, Clinic, User, get_db, utcnow

router = APIRouter(prefix="/clinic", tags=["clinic"])


class ClinicResponse(BaseModel):
    id: UUID
    name: str
    timezone: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ClinicUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Clinic name must contain at least 2 characters")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        value = value.strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Timezone must be a valid IANA timezone") from exc
        return value


@router.get("", response_model=ClinicResponse)
async def get_clinic(user: User = Depends(require_roles("clinic_manager", "administrator")), db: AsyncSession = Depends(get_db)) -> ClinicResponse:
    clinic = await db.get(Clinic, user.clinic_id)
    if not clinic:
        raise HTTPException(404, "Clinic not found")
    return clinic


@router.patch("", response_model=ClinicResponse)
async def update_clinic(payload: ClinicUpdate, user: User = Depends(require_roles("clinic_manager", "administrator")), db: AsyncSession = Depends(get_db)) -> ClinicResponse:
    clinic = await db.get(Clinic, user.clinic_id)
    if not clinic:
        raise HTTPException(404, "Clinic not found")
    changed = clinic.name != payload.name or clinic.timezone != payload.timezone
    clinic.name = payload.name
    clinic.timezone = payload.timezone
    clinic.updated_at = utcnow()
    if changed:
        db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="clinic", entity_id=clinic.id, action="settings_updated"))
    await db.commit()
    await db.refresh(clinic)
    return clinic

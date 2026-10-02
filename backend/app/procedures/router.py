from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import AuditEvent, ProcedureCatalog, User, get_db, utcnow

router = APIRouter(prefix="/procedures", tags=["procedures"])
MANAGERS = ("clinic_manager", "administrator")
STAFF_ROLES = ("clinic_manager", "doctor", "assistant", "reception", "administrator")

# Bounds
MAX_DURATION_MINUTES = 1440  # 24 hours
MIN_DURATION_MINUTES = 1
MAX_PRICE = Decimal("999999999.99")
MIN_PRICE = Decimal("0.00")


def normalize_code(value: str) -> str:
    """Normalize procedure code: strip whitespace and lowercase."""
    return value.strip().lower()


class ProcedureCatalogCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=80)
    default_duration_minutes: int = Field(ge=MIN_DURATION_MINUTES, le=MAX_DURATION_MINUTES)
    base_price: Decimal = Field(ge=MIN_PRICE, le=MAX_PRICE)
    currency: str = Field(default="RON", min_length=1, max_length=3)
    is_active: bool = Field(default=True)

    @field_validator("code", mode="before")
    @classmethod
    def clean_code(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Code cannot be empty")
        return value

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be empty")
        return value

    @field_validator("base_price", mode="before")
    @classmethod
    def parse_price(cls, value: str | Decimal) -> Decimal:
        if isinstance(value, str):
            value = Decimal(value)
        if value.as_tuple().exponent < -2:
            raise ValueError("Price must have at most 2 decimal places")
        return value


class ProcedureCatalogUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=80)
    default_duration_minutes: int | None = Field(default=None, ge=MIN_DURATION_MINUTES, le=MAX_DURATION_MINUTES)
    base_price: Decimal | None = Field(default=None, ge=MIN_PRICE, le=MAX_PRICE)
    currency: str | None = Field(default=None, min_length=1, max_length=3)

    @field_validator("code", mode="before")
    @classmethod
    def clean_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Code cannot be empty")
        return value

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be empty")
        return value

    @field_validator("base_price", mode="before")
    @classmethod
    def parse_price(cls, value: str | Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = Decimal(value)
        if value.as_tuple().exponent < -2:
            raise ValueError("Price must have at most 2 decimal places")
        return value


class ProcedureCatalogResponse(BaseModel):
    id: UUID
    clinic_id: UUID
    code: str
    name: str
    description: str | None
    category: str | None
    default_duration_minutes: int
    base_price: str
    currency: str
    is_active: bool
    created_at: str
    updated_at: str
    model_config = ConfigDict(from_attributes=True)


def response(procedure: ProcedureCatalog) -> ProcedureCatalogResponse:
    return ProcedureCatalogResponse.model_validate(
        {
            **procedure.__dict__,
            "base_price": str(procedure.base_price),
            "created_at": procedure.created_at.isoformat(),
            "updated_at": procedure.updated_at.isoformat(),
        },
        from_attributes=True,
    )


@router.get("", response_model=list[ProcedureCatalogResponse])
async def list_procedures(
    search: str | None = Query(default=None, max_length=100),
    active_only: bool = Query(default=True),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*STAFF_ROLES)),
) -> list[ProcedureCatalogResponse]:
    query = select(ProcedureCatalog).where(ProcedureCatalog.clinic_id == user.clinic_id).order_by(ProcedureCatalog.is_active.desc(), ProcedureCatalog.code).offset(offset).limit(limit)
    if active_only:
        query = query.where(ProcedureCatalog.is_active.is_(True))
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                ProcedureCatalog.code.ilike(term),
                ProcedureCatalog.name.ilike(term),
                ProcedureCatalog.category.ilike(term),
                ProcedureCatalog.description.ilike(term),
            )
        )
    return [response(item) for item in (await db.scalars(query)).all()]


@router.get("/{procedure_id}", response_model=ProcedureCatalogResponse)
async def get_procedure(
    procedure_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*STAFF_ROLES)),
) -> ProcedureCatalogResponse:
    procedure = await db.scalar(select(ProcedureCatalog).where(ProcedureCatalog.id == procedure_id, ProcedureCatalog.clinic_id == user.clinic_id))
    if not procedure:
        raise HTTPException(404, "Procedure not found")
    return response(procedure)


@router.post("", response_model=ProcedureCatalogResponse, status_code=status.HTTP_201_CREATED)
async def create_procedure(
    payload: ProcedureCatalogCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
) -> ProcedureCatalogResponse:
    code = payload.code.strip()
    code_lower = normalize_code(code)
    if await db.scalar(select(ProcedureCatalog.id).where(ProcedureCatalog.clinic_id == user.clinic_id, ProcedureCatalog.code_lower == code_lower)):
        raise HTTPException(409, "Procedure code already exists (case-insensitive)")
    procedure = ProcedureCatalog(
        clinic_id=user.clinic_id,
        code=code,
        code_lower=code_lower,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        category=payload.category.strip() if payload.category else None,
        default_duration_minutes=payload.default_duration_minutes,
        base_price=payload.base_price,
        currency=payload.currency.upper() if payload.currency else "RON",
        is_active=payload.is_active,
    )
    db.add(procedure)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Procedure code already exists (case-insensitive)") from exc
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="procedure_catalog", entity_id=procedure.id, action="created"))
    await db.commit()
    await db.refresh(procedure)
    return response(procedure)


@router.patch("/{procedure_id}", response_model=ProcedureCatalogResponse)
async def update_procedure(
    procedure_id: UUID,
    payload: ProcedureCatalogUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
) -> ProcedureCatalogResponse:
    procedure = await db.scalar(select(ProcedureCatalog).where(ProcedureCatalog.id == procedure_id, ProcedureCatalog.clinic_id == user.clinic_id).with_for_update())
    if not procedure:
        raise HTTPException(404, "Procedure not found")
    if payload.code is not None:
        code = payload.code.strip()
        code_lower = normalize_code(code)
        if code_lower != procedure.code_lower:
            if await db.scalar(select(ProcedureCatalog.id).where(ProcedureCatalog.clinic_id == user.clinic_id, ProcedureCatalog.code_lower == code_lower, ProcedureCatalog.id != procedure.id)):
                raise HTTPException(409, "Procedure code already exists (case-insensitive)")
            procedure.code = code
            procedure.code_lower = code_lower
    if payload.name is not None:
        procedure.name = payload.name.strip()
    if payload.description is not None:
        procedure.description = payload.description.strip() if payload.description else None
    if payload.category is not None:
        procedure.category = payload.category.strip() if payload.category else None
    if payload.default_duration_minutes is not None:
        procedure.default_duration_minutes = payload.default_duration_minutes
    if payload.base_price is not None:
        procedure.base_price = payload.base_price
    if payload.currency is not None:
        procedure.currency = payload.currency.upper()
    procedure.updated_at = utcnow()
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Procedure code already exists (case-insensitive)") from exc
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="procedure_catalog", entity_id=procedure.id, action="updated"))
    await db.commit()
    await db.refresh(procedure)
    return response(procedure)


@router.post("/{procedure_id}/activate", response_model=ProcedureCatalogResponse)
async def activate_procedure(
    procedure_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
) -> ProcedureCatalogResponse:
    procedure = await db.scalar(select(ProcedureCatalog).where(ProcedureCatalog.id == procedure_id, ProcedureCatalog.clinic_id == user.clinic_id).with_for_update())
    if not procedure:
        raise HTTPException(404, "Procedure not found")
    if procedure.is_active:
        raise HTTPException(409, "Procedure is already active")
    procedure.is_active = True
    procedure.updated_at = utcnow()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="procedure_catalog", entity_id=procedure.id, action="activated"))
    await db.commit()
    await db.refresh(procedure)
    return response(procedure)


@router.post("/{procedure_id}/deactivate", response_model=ProcedureCatalogResponse)
async def deactivate_procedure(
    procedure_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
) -> ProcedureCatalogResponse:
    procedure = await db.scalar(select(ProcedureCatalog).where(ProcedureCatalog.id == procedure_id, ProcedureCatalog.clinic_id == user.clinic_id).with_for_update())
    if not procedure:
        raise HTTPException(404, "Procedure not found")
    if not procedure.is_active:
        raise HTTPException(409, "Procedure is already inactive")
    procedure.is_active = False
    procedure.updated_at = utcnow()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="procedure_catalog", entity_id=procedure.id, action="deactivated"))
    await db.commit()
    await db.refresh(procedure)
    return response(procedure)

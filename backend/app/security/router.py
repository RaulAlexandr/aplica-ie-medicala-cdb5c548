from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_current_user, require_roles
from app.database import (
    AccessLog,
    AuditEvent,
    Base,
    ClinicalDocument,
    LoginUnlock,
    Patient,
    RefreshToken,
    User,
    get_db,
    utcnow,
)

router = APIRouter(prefix="/security", tags=["security"])
manager = require_roles("clinic_manager", "administrator")


@router.get("/access-log")
async def access_log(patient_id: UUID | None = None, actor_id: UUID | None = None,
    start: datetime | None = None, end: datetime | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    user: User = Depends(manager), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    query = select(AccessLog).where(AccessLog.clinic_id == user.clinic_id)
    if patient_id: query = query.where(AccessLog.patient_id == patient_id)
    if actor_id: query = query.where(AccessLog.actor_id == actor_id)
    if start: query = query.where(AccessLog.occurred_at >= start)
    if end: query = query.where(AccessLog.occurred_at <= end)
    rows = (await db.scalars(query.order_by(AccessLog.occurred_at.desc(), AccessLog.id).offset(offset).limit(limit + 1))).all()
    return {"items": [{"id": str(row.id), "patient_id": str(row.patient_id), "actor_id": str(row.actor_id),
        "action": row.action, "occurred_at": row.occurred_at.isoformat()} for row in rows[:limit]],
        "has_more": len(rows) > limit, "limit": limit, "offset": offset}


def encode(value: Any) -> Any:
    if isinstance(value, UUID): return str(value)
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, (date, time)): return value.isoformat()
    if isinstance(value, Decimal): return str(value)
    return value


@router.get("/patients/{patient_id}/export")
async def export_patient(patient_id: UUID, user: User = Depends(require_roles("clinic_manager")), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient: raise HTTPException(404, "Patient not found")
    data: dict[str, list[dict[str, Any]]] = {}
    known: dict[str, set[Any]] = {Patient.__tablename__: {patient_id}}
    collected: dict[str, dict[Any, Any]] = {Patient.__tablename__: {patient.id: patient}}
    mappers = list(Base.registry.mappers)
    changed = True
    while changed:
        changed = False
        for mapper in mappers:
            model = mapper.class_
            table = model.__tablename__
            if table == Patient.__tablename__:
                continue
            clauses = []
            for foreign_key in model.__table__.foreign_keys:
                target = foreign_key.column.table.name
                if known.get(target):
                    clauses.append(getattr(model, foreign_key.parent.key).in_(known[target]))
            if not clauses:
                continue
            statement = select(model).where(or_(*clauses))
            if hasattr(model, "clinic_id"):
                statement = statement.where(model.clinic_id == user.clinic_id)
            rows = (await db.scalars(statement)).all()
            bucket = collected.setdefault(table, {})
            for row in rows:
                row_id = tuple(getattr(row, key.key) for key in mapper.primary_key)
                row_id = row_id[0] if len(row_id) == 1 else row_id
                if row_id not in bucket:
                    bucket[row_id] = row
                    known.setdefault(table, set()).add(row_id)
                    changed = True
    for table, rows in collected.items():
        mapper = next(m for m in mappers if m.class_.__tablename__ == table)
        model = mapper.class_
        data[table] = [{column.key: encode(getattr(row, column.key)) for column in mapper.columns
            if not (model is ClinicalDocument and column.key == "storage_key")} for row in rows.values()]
    docs = (await db.scalars(select(ClinicalDocument).where(ClinicalDocument.patient_id == patient_id,
        ClinicalDocument.clinic_id == user.clinic_id))).all()
    data["documents_index"] = [{"id": str(doc.id), "file_name": doc.file_name, "sha256": doc.sha256,
        "size_bytes": doc.size_bytes, "content_type": doc.content_type} for doc in docs]
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="patient", entity_id=patient_id, action="exported"))
    await db.commit()
    return {"schema": "dentacare.patient-export.v1", "exported_at": utcnow().isoformat(),
        "patient_id": str(patient_id), "data": data}


@router.post("/staff/{staff_id}/unlock", status_code=204)
async def unlock_staff(staff_id: UUID, user: User = Depends(manager), db: AsyncSession = Depends(get_db)) -> None:
    staff = await db.scalar(select(User).where(User.id == staff_id, User.clinic_id == user.clinic_id))
    if not staff: raise HTTPException(404, "Staff member not found")
    db.add(LoginUnlock(email=staff.email, actor_id=user.id))
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="staff", entity_id=staff.id, action="login_unlocked"))
    await db.commit()


@router.get("/sessions")
async def sessions(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id,
        RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > utcnow()).order_by(RefreshToken.created_at.desc()))).all()
    latest = {}
    for row in rows: latest.setdefault(row.session_id, row)
    return [{"session_id": str(sid), "created_at": row.created_at.isoformat(),
        "last_used_at": (row.last_used_at or row.created_at).isoformat(),
        "current": sid == getattr(user, "current_session_id", None)} for sid, row in latest.items()]


@router.delete("/sessions/others", status_code=204)
async def close_other_sessions(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> None:
    sid = getattr(user, "current_session_id", None)
    rows = (await db.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id,
        RefreshToken.revoked_at.is_(None), RefreshToken.session_id != (sid or UUID(int=0))))).all()
    for row in rows: row.revoked_at = utcnow()
    await db.commit()


@router.delete("/sessions/{session_id}", status_code=204)
async def close_session(session_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> None:
    if session_id == getattr(user, "current_session_id", None): raise HTTPException(409, "Use logout to close the current session")
    rows = (await db.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id,
        RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None)))).all()
    if not rows: raise HTTPException(404, "Session not found")
    for row in rows: row.revoked_at = utcnow()
    await db.commit()

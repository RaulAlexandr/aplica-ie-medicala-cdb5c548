from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.automations.models import AutomationExecution, AutomationRule, AutomationRuleVersion
from app.automations.service import (
    _perform,
    evaluate,
    resolve_assignee,
    simulate,
    validate_definition,
)
from app.database import User, get_db, utcnow

router = APIRouter(prefix="/automations", tags=["automations"])
MANAGERS = ("clinic_manager", "administrator")


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    trigger: str = Field(min_length=1, max_length=40)
    conditions: list[dict] = Field(default_factory=list, max_length=12)
    action: dict
    mode: str

    @model_validator(mode="after")
    def validate_rule(self):
        try:
            validate_definition(name=self.name, trigger=self.trigger, conditions=self.conditions, action=self.action, mode=self.mode)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class ActivateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_at: datetime | None = None

    @field_validator("start_at")
    @classmethod
    def aware_start(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Data de start trebuie să includă fusul orar")
        return value


class SimulationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(ge=1, le=365)


class UnsavedSimulationInput(RuleInput):
    days: int = Field(default=30, ge=1, le=365)


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("decision")
    @classmethod
    def supported_decision(cls, value):
        if value not in {"approve", "reject"}:
            raise ValueError("Alege aprobă sau respinge")
        return value


def _version_out(rule: AutomationRule, version: AutomationRuleVersion) -> dict:
    return {"id": str(rule.id), "version_id": str(version.id), "version": version.version, "name": version.name,
            "trigger": version.trigger, "conditions": version.conditions, "action": version.action, "mode": version.mode,
            "active": version.is_active, "start_at": version.start_at.isoformat() if version.start_at else None,
            "cursor_at": version.cursor_at.isoformat() if version.cursor_at else None, "created_at": version.created_at.isoformat()}


def _execution_out(run: AutomationExecution) -> dict:
    return {"id": str(run.id), "rule_id": str(run.rule_id), "version_id": str(run.version_id), "event_type": run.event_type,
            "event_id": run.event_id, "occurred_at": run.occurred_at.isoformat(), "event": run.event_data,
            "conditions": run.conditions_result, "action": run.action_snapshot, "status": run.status,
            "attempt_count": run.attempt_count, "reason": run.reason, "result": run.result,
            "decided_by_id": str(run.decided_by_id) if run.decided_by_id else None,
            "decided_at": run.decided_at.isoformat() if run.decided_at else None, "decision_reason": run.decision_reason,
            "created_at": run.created_at.isoformat(), "updated_at": run.updated_at.isoformat()}


async def _owned_rule(db: AsyncSession, clinic_id: UUID, rule_id: UUID) -> tuple[AutomationRule, AutomationRuleVersion]:
    rule = await db.scalar(select(AutomationRule).where(AutomationRule.id == rule_id, AutomationRule.clinic_id == clinic_id))
    if rule is None:
        raise HTTPException(404, "Regula nu a fost găsită")
    version = await db.scalar(select(AutomationRuleVersion).where(AutomationRuleVersion.rule_id == rule.id,
        AutomationRuleVersion.clinic_id == clinic_id, AutomationRuleVersion.version == rule.current_version))
    if version is None:
        raise HTTPException(404, "Regula nu a fost găsită")
    return rule, version


async def _validate_target(db: AsyncSession, clinic_id: UUID, payload: RuleInput) -> None:
    if payload.action["target_type"] == "user":
        user = await db.scalar(select(User.id).where(User.id == UUID(payload.action["target_value"]), User.clinic_id == clinic_id, User.is_active.is_(True)))
        if user is None:
            raise HTTPException(422, "Alege o persoană activă din clinica ta")


@router.get("")
async def list_rules(user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(AutomationRule, AutomationRuleVersion).join(AutomationRuleVersion,
        (AutomationRuleVersion.rule_id == AutomationRule.id) & (AutomationRuleVersion.version == AutomationRule.current_version))
        .where(AutomationRule.clinic_id == user.clinic_id).order_by(AutomationRule.created_at.desc(), AutomationRule.id))).all()
    return [_version_out(rule, version) for rule, version in rows]


@router.post("", status_code=201)
async def create_rule(payload: RuleInput, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    await _validate_target(db, user.clinic_id, payload)
    now, rule_id, version_id = utcnow(), uuid4(), uuid4()
    rule = AutomationRule(id=rule_id, clinic_id=user.clinic_id, current_version=1, created_by_id=user.id, created_at=now)
    version = AutomationRuleVersion(id=version_id, clinic_id=user.clinic_id, rule_id=rule_id, version=1, name=payload.name.strip(), trigger=payload.trigger,
        conditions=payload.conditions, action=payload.action, mode=payload.mode, is_active=False, created_by_id=user.id, created_at=now)
    db.add_all([rule, version])
    await db.commit()
    return _version_out(rule, version)


@router.get("/{rule_id}")
async def get_rule(rule_id: UUID, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    rule, version = await _owned_rule(db, user.clinic_id, rule_id)
    return _version_out(rule, version)


@router.put("/{rule_id}")
async def revise_rule(rule_id: UUID, payload: RuleInput, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    rule, current = await _owned_rule(db, user.clinic_id, rule_id)
    await _validate_target(db, user.clinic_id, payload)
    now, was_active = utcnow(), current.is_active
    current.is_active = False
    version = AutomationRuleVersion(id=uuid4(), clinic_id=user.clinic_id, rule_id=rule.id, version=current.version + 1,
        name=payload.name.strip(), trigger=payload.trigger, conditions=payload.conditions, action=payload.action, mode=payload.mode,
        is_active=was_active, cursor_at=now if was_active else None, start_at=now if was_active else None, created_by_id=user.id, created_at=now)
    rule.current_version = version.version
    db.add(version)
    await db.commit()
    return _version_out(rule, version)


@router.post("/{rule_id}/activate")
async def activate_rule(rule_id: UUID, payload: ActivateInput, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    rule, version = await _owned_rule(db, user.clinic_id, rule_id)
    if version.action["target_type"] == "user":
        active_user = await db.scalar(select(User.id).where(User.id == UUID(version.action["target_value"]), User.clinic_id == user.clinic_id, User.is_active.is_(True)))
        if active_user is None:
            raise HTTPException(422, "Responsabilul ales nu mai este activ în clinica ta")
    start = payload.start_at.astimezone(UTC) if payload.start_at else utcnow()
    for other in (await db.scalars(select(AutomationRuleVersion).where(AutomationRuleVersion.rule_id == rule.id, AutomationRuleVersion.clinic_id == user.clinic_id))).all():
        other.is_active = other.id == version.id
    version.is_active, version.start_at, version.cursor_at = True, start, start
    await db.commit()
    return _version_out(rule, version)


@router.post("/{rule_id}/stop")
async def stop_rule(rule_id: UUID, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    rule, version = await _owned_rule(db, user.clinic_id, rule_id)
    version.is_active = False
    pending = (await db.scalars(select(AutomationExecution).where(AutomationExecution.rule_id == rule.id, AutomationExecution.clinic_id == user.clinic_id,
        AutomationExecution.status.in_(["pending_approval", "failed_retryable"])))).all()
    for run in pending:
        run.status, run.reason, run.updated_at = "stopped", "Regula a fost oprită în timp ce execuția aștepta o acțiune.", utcnow()
    await db.commit()
    return _version_out(rule, version)


@router.post("/evaluate")
async def evaluate_rules(user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    return await evaluate(db, user.clinic_id, user.id)


@router.post("/simulate")
async def simulate_unsaved(payload: UnsavedSimulationInput, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    return await simulate(db, user.clinic_id, payload.trigger, payload.conditions, payload.action, payload.days)


@router.post("/{rule_id}/simulate")
async def simulate_saved(rule_id: UUID, payload: SimulationInput, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    _, version = await _owned_rule(db, user.clinic_id, rule_id)
    return await simulate(db, user.clinic_id, version.trigger, version.conditions, version.action, payload.days)


@router.get("/{rule_id}/executions")
async def list_executions(rule_id: UUID, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rule, _ = await _owned_rule(db, user.clinic_id, rule_id)
    runs = (await db.scalars(select(AutomationExecution).where(AutomationExecution.rule_id == rule.id, AutomationExecution.clinic_id == user.clinic_id)
        .order_by(AutomationExecution.created_at.desc(), AutomationExecution.id).limit(200))).all()
    return [_execution_out(run) for run in runs]


@router.get("/executions/pending")
async def pending_approvals(user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> list[dict]:
    runs = (await db.scalars(select(AutomationExecution).where(AutomationExecution.clinic_id == user.clinic_id,
        AutomationExecution.status == "pending_approval").order_by(AutomationExecution.created_at, AutomationExecution.id).limit(200))).all()
    return [_execution_out(run) for run in runs]


@router.post("/executions/{execution_id}/decision")
async def decide(execution_id: UUID, payload: DecisionInput, user: User = Depends(require_roles(*MANAGERS)), db: AsyncSession = Depends(get_db)) -> dict:
    run = await db.scalar(select(AutomationExecution).where(AutomationExecution.id == execution_id, AutomationExecution.clinic_id == user.clinic_id).with_for_update())
    if run is None:
        raise HTTPException(404, "Execuția nu a fost găsită")
    if run.status != "pending_approval":
        raise HTTPException(409, "Execuția nu mai așteaptă aprobarea")
    run.decided_by_id, run.decided_at, run.decision_reason = user.id, utcnow(), payload.reason
    if payload.decision == "reject":
        run.status, run.reason = "rejected", payload.reason or "Respinsă de manager"
    else:
        assignee = await resolve_assignee(db, user.clinic_id, run.action_snapshot)
        await _perform(db, run, user.id, assignee)
    await db.commit()
    return _execution_out(run)

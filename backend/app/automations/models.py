from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow

JSONValue = JSON().with_variant(JSONB, "postgresql")


class AutomationRule(Base):
    __tablename__ = "automation_rules"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_automation_rules_clinic", "clinic_id", "created_at"), CheckConstraint("current_version > 0", name="ck_automation_rule_version"))


class AutomationRuleVersion(Base):
    __tablename__ = "automation_rule_versions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("automation_rules.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    conditions: Mapped[list] = mapped_column(JSONValue, nullable=False, default=list)
    action: Mapped[dict] = mapped_column(JSONValue, nullable=False)
    mode: Mapped[str] = mapped_column(String(12), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=False, nullable=False)
    cursor_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("rule_id", "version", name="uq_automation_rule_version"),
        Index("ix_automation_versions_active", "clinic_id", "is_active", "trigger"),
        CheckConstraint("version > 0", name="ck_automation_version_number"),
        CheckConstraint("mode IN ('automatic', 'approval')", name="ck_automation_version_mode"),
    )


class AutomationExecution(Base):
    __tablename__ = "automation_executions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("automation_rules.id", ondelete="RESTRICT"), nullable=False)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("automation_rule_versions.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    event_id: Mapped[str] = mapped_column(String(80), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    event_data: Mapped[dict] = mapped_column(JSONValue, nullable=False)
    conditions_result: Mapped[list] = mapped_column(JSONValue, nullable=False)
    action_snapshot: Mapped[dict] = mapped_column(JSONValue, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONValue)
    decided_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("version_id", "event_type", "event_id", name="uq_automation_execution_event"),
        Index("ix_automation_exec_clinic_status", "clinic_id", "status", "created_at"),
        Index("ix_automation_exec_version_time", "version_id", "occurred_at"),
        CheckConstraint("status IN ('pending_approval','running','succeeded','failed_retryable','failed','rejected','stopped')", name="ck_automation_execution_status"),
        CheckConstraint("attempt_count BETWEEN 0 AND 3", name="ck_automation_execution_attempts"),
    )

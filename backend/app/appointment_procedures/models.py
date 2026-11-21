from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow


class AppointmentProcedure(Base):
    __tablename__ = "appointment_procedures"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False, index=True)
    procedure_catalog_id: Mapped[UUID] = mapped_column(ForeignKey("procedure_catalog.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    added_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    removed_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (
        CheckConstraint("quantity >= 1 AND quantity <= 32", name="ck_appointment_procedure_quantity"),
        Index("uq_appointment_procedure_active", "appointment_id", "procedure_catalog_id", unique=True,
              postgresql_where=removed_at.is_(None), sqlite_where=removed_at.is_(None)),
    )


class AppointmentProcedureRevision(Base):
    __tablename__ = "appointment_procedure_revisions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("appointment_id", name="uq_appointment_procedure_revision_appointment"),
                      CheckConstraint("revision >= 0", name="ck_appointment_procedure_revision_nonnegative"))

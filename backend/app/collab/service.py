"""Shared internal task creation used by collaboration and automation workflows."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.collab.models import CollabTask, CollabTaskEvent
from app.database import AuditEvent, utcnow


async def create_task(db: AsyncSession, *, clinic_id: UUID, actor_id: UUID, assignee_id: UUID, title: str,
                      due_at: datetime | None, description: str | None = None) -> CollabTask:
    """Create the task, its first immutable event, and its audit record atomically."""
    now, task_id = utcnow(), uuid4()
    task = CollabTask(id=task_id, clinic_id=clinic_id, title=title, description=description, assignee_id=assignee_id,
                      due_at=due_at, status="open", version=1, created_by_id=actor_id, created_at=now, updated_at=now)
    db.add(task)
    db.add(CollabTaskEvent(id=uuid4(), clinic_id=clinic_id, task_id=task_id, version=1, from_status=None, to_status="open",
        from_assignee_id=None, to_assignee_id=assignee_id, reason="Sarcină creată", actor_id=actor_id, created_at=now))
    db.add(AuditEvent(clinic_id=clinic_id, actor_id=actor_id, entity_type="collab_task", entity_id=task_id, action="created"))
    await db.flush()
    return task

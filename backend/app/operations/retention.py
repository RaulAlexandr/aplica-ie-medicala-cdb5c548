import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AccessLog, LoginAttempt, SessionLocal


async def cleanup_security_history(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    login_days: int | None = None,
    access_days: int | None = None,
    dry_run: bool = True,
) -> dict:
    if (login_days if login_days is not None else settings.login_attempt_retention_days) < 1:
        raise ValueError("Păstrarea încercărilor trebuie să fie de cel puțin o zi")
    if (access_days if access_days is not None else settings.patient_access_log_retention_days) < 1:
        raise ValueError("Păstrarea jurnalului de acces trebuie să fie de cel puțin o zi")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    login_cutoff = current - timedelta(days=login_days or settings.login_attempt_retention_days)
    access_cutoff = current - timedelta(days=access_days or settings.patient_access_log_retention_days)
    conditions = {
        "login_attempts": (LoginAttempt, LoginAttempt.attempted_at, login_cutoff),
        "patient_access_log": (AccessLog, AccessLog.occurred_at, access_cutoff),
    }
    counts = {}
    for name, (model, timestamp, cutoff) in conditions.items():
        counts[name] = int(await db.scalar(select(func.count()).select_from(model).where(timestamp < cutoff)) or 0)
    deleted = {name: 0 for name in conditions}
    if not dry_run:
        if db.bind and db.bind.dialect.name == "postgresql":
            await db.execute(text("SELECT set_config('dentacare.security_retention', 'on', true)"))
            await db.execute(text("SELECT set_config('dentacare.login_attempt_cutoff', :cutoff, true)"),
                             {"cutoff": login_cutoff.isoformat()})
            await db.execute(text("SELECT set_config('dentacare.patient_access_cutoff', :cutoff, true)"),
                             {"cutoff": access_cutoff.isoformat()})
        for name, (model, timestamp, cutoff) in conditions.items():
            result = await db.execute(delete(model).where(timestamp < cutoff))
            deleted[name] = max(result.rowcount or 0, 0)
        await db.commit()
    return {"dry_run": dry_run, "cutoffs": {"login_attempts": login_cutoff.isoformat(),
        "patient_access_log": access_cutoff.isoformat()}, "counts": counts, "deleted": deleted}


async def _run(dry_run: bool) -> dict:
    async with SessionLocal() as db:
        return await cleanup_security_history(db, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="Curăță doar încercările de autentificare și jurnalul T34 expirate.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Afișează numărul de rânduri expirate fără ștergere (implicit).")
    mode.add_argument("--apply", action="store_true", help="Șterge numai rândurile de securitate mai vechi decât retenția configurată.")
    args = parser.parse_args()
    report = asyncio.run(_run(dry_run=not args.apply))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

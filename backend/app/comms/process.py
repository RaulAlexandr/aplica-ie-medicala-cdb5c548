"""Send the due messages once and exit: python -m app.comms.process (no permanent worker)."""
import asyncio

from app.comms.service import process_due
from app.database import SessionLocal


async def run() -> dict[str, int]:
    async with SessionLocal() as db:
        return await process_due(db)


if __name__ == "__main__":
    print(asyncio.run(run()))

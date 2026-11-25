"""Channel adapters. Only the simulated one ships: it sends nothing outside the process."""
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from app.config import settings


class AdapterError(Exception):
    """The provider refused or could not take the message; the outbox retries up to its limit."""


@dataclass(frozen=True)
class SendResult:
    provider_ref: str


class CommAdapter(Protocol):
    name: str

    async def send(self, channel: str, recipient: str, subject: str, body: str) -> SendResult: ...


class SimulatedAdapter:
    """Accepts every message and returns a fake reference; the final text stays in the outbox row."""
    name = "simulated"

    async def send(self, channel: str, recipient: str, subject: str, body: str) -> SendResult:
        return SendResult(provider_ref=f"sim-{uuid4().hex[:16]}")


ADAPTERS: dict[str, type] = {"simulated": SimulatedAdapter}


def get_adapter() -> CommAdapter:
    try:
        return ADAPTERS[settings.comms_adapter]()
    except KeyError as exc:
        raise RuntimeError(f"Unknown communication adapter: {settings.comms_adapter!r}") from exc

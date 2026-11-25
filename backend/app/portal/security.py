"""Portal tokens, sessions and login throttling.

Portal access tokens are signed with a key derived only for the portal, carry ``typ=portal`` and the
portal audience, and name the session. A staff token fails here (other key, no ``typ``), and a portal
token fails on every staff route (``get_current_user`` rejects it). Each request reloads the session
and the account, so a logout or a revocation by staff ends access immediately.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import Clinic, get_db, utcnow
from app.portal.models import PortalAccount, PortalLoginAttempt, PortalSession

ALGORITHM = "HS256"
TOKEN_TYPE = "portal"
AUDIENCE = "dentacare-portal"
INVITATION_HOURS = 72
SESSION_DAYS = 14
LOCK_ATTEMPTS = 5
LOCK_WINDOW = timedelta(minutes=15)
GENERIC_LOGIN_ERROR = "Email sau parolă incorectă."
_bearer = HTTPBearer(auto_error=False)


def portal_key() -> str:
    return hmac.new(settings.jwt_secret.encode(), b"dentacare-portal-access-v1", hashlib.sha256).hexdigest()


def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_secret() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, token_digest(raw)


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def create_access_token(account: PortalAccount, session: PortalSession) -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(account.id), "sid": str(session.id), "cid": str(account.clinic_id), "typ": TOKEN_TYPE,
              "aud": AUDIENCE, "iat": int(now.timestamp()), "exp": now + timedelta(minutes=settings.access_token_minutes)}
    return jwt.encode(claims, portal_key(), algorithm=ALGORITHM)


@dataclass
class PortalContext:
    account: PortalAccount
    session: PortalSession
    clinic: Clinic


async def get_portal_context(credentials=Depends(_bearer), db: AsyncSession = Depends(get_db)) -> PortalContext:
    if not credentials:
        raise HTTPException(401, "Autentificare necesară.")
    try:
        payload = jwt.decode(credentials.credentials, portal_key(), algorithms=[ALGORITHM], audience=AUDIENCE)
        if payload.get("typ") != TOKEN_TYPE or payload.get("aud") != AUDIENCE:
            raise JWTError("not a portal token")
        account_id, session_id, clinic_id = UUID(payload["sub"]), UUID(payload["sid"]), UUID(payload["cid"])
    except (JWTError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(401, "Sesiune invalidă. Autentifică-te din nou.") from exc
    row = (await db.execute(select(PortalSession, PortalAccount, Clinic).join(
        PortalAccount, PortalAccount.id == PortalSession.account_id,
    ).join(Clinic, Clinic.id == PortalAccount.clinic_id).where(
        PortalSession.id == session_id, PortalSession.account_id == account_id,
        PortalAccount.clinic_id == clinic_id, PortalSession.clinic_id == clinic_id,
    ))).first()
    if not row:
        raise HTTPException(401, "Sesiune invalidă. Autentifică-te din nou.")
    session, account, clinic = row
    if session.revoked_at is not None or aware(session.expires_at) <= utcnow() or account.status != "active":
        raise HTTPException(401, "Sesiunea s-a încheiat. Autentifică-te din nou.")
    return PortalContext(account=account, session=session, clinic=clinic)


async def is_locked(db: AsyncSession, clinic_id: UUID, email: str) -> bool:
    """Locked for 15 minutes after 5 failures within 15 minutes since the last success."""
    now = utcnow()
    last_success = await db.scalar(select(PortalLoginAttempt.occurred_at).where(
        PortalLoginAttempt.clinic_id == clinic_id, PortalLoginAttempt.email == email,
        PortalLoginAttempt.outcome == "success",
    ).order_by(PortalLoginAttempt.occurred_at.desc()).limit(1))
    query = select(PortalLoginAttempt.occurred_at).where(
        PortalLoginAttempt.clinic_id == clinic_id, PortalLoginAttempt.email == email,
        PortalLoginAttempt.outcome == "failure", PortalLoginAttempt.occurred_at > now - 2 * LOCK_WINDOW,
    )
    if last_success is not None:
        query = query.where(PortalLoginAttempt.occurred_at > last_success)
    failures = [aware(value) for value in (await db.scalars(
        query.order_by(PortalLoginAttempt.occurred_at.desc()).limit(LOCK_ATTEMPTS))).all()]
    if len(failures) < LOCK_ATTEMPTS:
        return False
    newest, oldest = failures[0], failures[-1]
    return newest - oldest <= LOCK_WINDOW and now < newest + LOCK_WINDOW

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import RefreshToken, User, get_db, utcnow

password_hash = PasswordHash.recommended()
ALGORITHM = "HS256"
ROLES = {"clinic_manager", "doctor", "assistant", "reception", "administrator"}
STAFF_ROLES = ROLES


def hash_password(value: str) -> str:
    return password_hash.hash(value)


def verify_password(value: str, hashed: str) -> bool:
    return password_hash.verify(value, hashed)


ISSUED_AT_CLAIM = "iat_us"


def _epoch_us(value: datetime) -> int:
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def create_access_token(user: User) -> str:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.access_token_minutes)
    # Microsecond issue time: lets a reactivated account reject tokens issued before its last deactivation.
    claims = {"sub": str(user.id), "exp": expires, ISSUED_AT_CLAIM: _epoch_us(now)}
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def issued_before_deactivation(payload: dict, user: User) -> bool:
    """True when the token predates the user's last deactivation (or cannot prove otherwise)."""
    if user.deactivated_at is None:
        return False
    issued = payload.get(ISSUED_AT_CLAIM)
    return not isinstance(issued, int) or issued <= _epoch_us(user.deactivated_at)


async def revoke_refresh_tokens(db: AsyncSession, user_id: UUID) -> int:
    records = (await db.scalars(select(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)).with_for_update())).all()
    now = utcnow()
    for record in records:
        record.revoked_at = now
    return len(records)


def _hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_refresh_token(db: AsyncSession, user: User) -> str:
    raw = secrets.token_urlsafe(48)
    db.add(RefreshToken(user_id=user.id, token_hash=_hash_refresh(raw), expires_at=utcnow() + timedelta(days=settings.refresh_token_days)))
    await db.flush()
    return raw


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await db.scalar(select(User).where(User.email == email.lower()))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return None
    return user


async def lock_user(db: AsyncSession, user_id: UUID) -> User | None:
    """Lock the user row and reload it, so is_active reflects any deactivation committed meanwhile.

    Lock order on every path that touches refresh tokens (login, refresh, logout, staff deactivation and
    reactivation): the users row first, then refresh_tokens rows. Inserting a refresh token takes a key-share
    lock on the user through the foreign key, so the opposite order can deadlock with a deactivation.
    """
    return await db.scalar(select(User).where(User.id == user_id).with_for_update()
                           .execution_options(populate_existing=True))


async def rotate_refresh_token(db: AsyncSession, raw: str) -> User:
    token_hash = _hash_refresh(raw)
    owner_id = await db.scalar(select(RefreshToken.user_id).where(RefreshToken.token_hash == token_hash))
    user = await lock_user(db, owner_id) if owner_id else None
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    record = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
                             .execution_options(populate_existing=True))
    expires_at = record.expires_at if record and record.expires_at.tzinfo else (record.expires_at.replace(tzinfo=UTC) if record else None)
    if not record or record.user_id != user.id or record.revoked_at is not None or expires_at <= utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    record.revoked_at = utcnow()
    return user


bearer = HTTPBearer(auto_error=False)


async def get_current_user(credentials=Depends(bearer), db: AsyncSession = Depends(get_db)) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[ALGORITHM])
        if "typ" in payload or "aud" in payload:
            raise JWTError("not a staff token")  # T14: portal tokens never open staff routes
        user_id = UUID(payload["sub"])
    except (JWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid access token") from exc
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or missing")
    if issued_before_deactivation(payload, user):
        raise HTTPException(status_code=401, detail="Session ended by deactivation; sign in again")
    return user


def require_roles(*roles: str):
    unknown = set(roles) - ROLES
    if unknown:
        raise ValueError(f"Unknown roles: {sorted(unknown)}")
    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dependency

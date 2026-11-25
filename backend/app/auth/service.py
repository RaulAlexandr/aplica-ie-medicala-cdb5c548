import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
from pwdlib import PasswordHash
from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import LoginAttempt, LoginUnlock, RefreshToken, User, get_db, utcnow

password_hash = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hash.hash("unusable timing equalization password")
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


def create_access_token(user: User, session_id: UUID | None = None) -> str:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.access_token_minutes)
    # Microsecond issue time: lets a reactivated account reject tokens issued before its last deactivation.
    claims = {"sub": str(user.id), "exp": expires, ISSUED_AT_CLAIM: _epoch_us(now)}
    if session_id:
        claims["sid"] = str(session_id)
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


async def issue_refresh_token(db: AsyncSession, user: User, session_id: UUID | None = None) -> str:
    raw = secrets.token_urlsafe(48)
    db.add(RefreshToken(user_id=user.id, session_id=session_id or uuid4(), token_hash=_hash_refresh(raw), expires_at=utcnow() + timedelta(days=settings.refresh_token_days), last_used_at=utcnow()))
    await db.flush()
    return raw


async def authenticate(db: AsyncSession, email: str, password: str, ip_address: str | None = None) -> User | None:
    normalized = email.strip().lower()
    # Serialize attempts per normalized email on PostgreSQL to keep the threshold correct under concurrency.
    if db.bind and db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:email, 0))"), {"email": normalized})
    now = utcnow()
    last_unlock = await db.scalar(select(LoginUnlock.unlocked_at).where(LoginUnlock.email == normalized)
                                  .order_by(LoginUnlock.unlocked_at.desc()).limit(1))
    attempts = select(LoginAttempt).where(LoginAttempt.email == normalized,
        LoginAttempt.attempted_at > now - timedelta(minutes=15), LoginAttempt.succeeded.is_(False))
    if last_unlock:
        attempts = attempts.where(LoginAttempt.attempted_at > last_unlock)
    recent = (await db.scalars(attempts.order_by(LoginAttempt.attempted_at.desc()).limit(5))).all()
    if len(recent) >= 5:
        verify_password(password, DUMMY_PASSWORD_HASH)
        db.add(LoginAttempt(email=normalized, ip_address=ip_address, attempted_at=now, succeeded=False))
        await db.commit()
        return None
    user = await db.scalar(select(User).where(User.email == normalized))
    password_valid = verify_password(password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    valid = bool(user and user.is_active and password_valid)
    db.add(LoginAttempt(email=normalized, ip_address=ip_address, attempted_at=now, succeeded=valid))
    await db.commit()
    return user if valid else None


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
    record.last_used_at = utcnow()
    user.current_session_id = record.session_id
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
    session_id = None
    try:
        session_id = UUID(payload["sid"]) if payload.get("sid") else None
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid access token") from exc
    active_session = exists(select(RefreshToken.id).where(RefreshToken.user_id == user_id,
        RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None),
        RefreshToken.expires_at > utcnow())) if session_id else None
    statement = select(User, active_session).where(User.id == user_id) if active_session is not None else select(User).where(User.id == user_id)
    result = await db.execute(statement)
    row = result.first()
    user = row[0] if row else None
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or missing")
    if issued_before_deactivation(payload, user):
        raise HTTPException(status_code=401, detail="Session ended by deactivation; sign in again")
    user.current_session_id = session_id
    issued_at = payload.get(ISSUED_AT_CLAIM)
    if user.password_changed_at and isinstance(issued_at, int) and issued_at < _epoch_us(user.password_changed_at) and not user.current_session_id:
        raise HTTPException(status_code=401, detail="Password changed; sign in again")
    if user.current_session_id and not row[1]:
        raise HTTPException(status_code=401, detail="Session ended; sign in again")
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

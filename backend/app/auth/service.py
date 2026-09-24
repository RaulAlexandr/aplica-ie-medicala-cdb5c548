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


def create_access_token(user: User) -> str:
    expires = datetime.now(UTC) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": str(user.id), "exp": expires}, settings.jwt_secret, algorithm=ALGORITHM)


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


async def rotate_refresh_token(db: AsyncSession, raw: str) -> User:
    record = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(raw)).with_for_update())
    expires_at = record.expires_at if record and record.expires_at.tzinfo else (record.expires_at.replace(tzinfo=UTC) if record else None)
    if not record or record.revoked_at is not None or expires_at <= utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    user = await db.get(User, record.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    record.revoked_at = utcnow()
    return user


bearer = HTTPBearer(auto_error=False)


async def get_current_user(credentials=Depends(bearer), db: AsyncSession = Depends(get_db)) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[ALGORITHM])
        user_id = UUID(payload["sub"])
    except (JWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid access token") from exc
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or missing")
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

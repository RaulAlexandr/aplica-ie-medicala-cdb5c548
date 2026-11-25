from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import (
    _hash_refresh,
    authenticate,
    create_access_token,
    get_current_user,
    hash_password,
    issue_refresh_token,
    lock_user,
    rotate_refresh_token,
    verify_password,
)
from app.database import Clinic, RefreshToken, User, get_db, utcnow

router = APIRouter(prefix="/auth", tags=["auth"])

class RegisterRequest(BaseModel):
    clinic_name: str = Field(min_length=2, max_length=160)
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)
class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=20)
class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=128)
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
class UserResponse(BaseModel):
    id: str
    clinic_id: str
    full_name: str
    email: EmailStr
    role: str

@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    if await db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(409, "Email already registered")
    clinic = Clinic(name=payload.clinic_name.strip())
    db.add(clinic)
    await db.flush()
    user = User(clinic_id=clinic.id, full_name=payload.full_name.strip(), email=payload.email.lower(), password_hash=hash_password(payload.password), role="clinic_manager")
    db.add(user)
    await db.flush()
    session_id = uuid4()
    refresh = await issue_refresh_token(db, user, session_id)
    await db.commit()
    return TokenResponse(access_token=create_access_token(user, session_id), refresh_token=refresh)

@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await authenticate(db, payload.email, payload.password, request.client.host if request.client else None)
    # Lock order users -> refresh_tokens; re-read is_active under the lock (a deactivation may have committed).
    user = await lock_user(db, user.id) if user else None
    if not user or not user.is_active:
        raise HTTPException(401, "Invalid email or password")
    session_id = uuid4()
    refresh = await issue_refresh_token(db, user, session_id)
    # Issued while the user row is locked: a deactivation waiting on that lock gets a later deactivated_at.
    access = create_access_token(user, session_id)
    await db.commit()
    return TokenResponse(access_token=access, refresh_token=refresh)

@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await rotate_refresh_token(db, payload.refresh_token)  # locks users, then refresh_tokens
    session_id = user.current_session_id
    new_refresh = await issue_refresh_token(db, user, session_id)
    access = create_access_token(user, session_id)
    await db.commit()
    return TokenResponse(access_token=access, refresh_token=new_refresh)

@router.post("/logout", status_code=204)
async def logout(payload: LogoutRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> None:
    await lock_user(db, user.id)  # same lock order as every other refresh-token path
    token = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(payload.refresh_token), RefreshToken.user_id == user.id).with_for_update())
    if token and token.revoked_at is None:
        token.revoked_at = utcnow()
        await db.commit()

@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=str(user.id), clinic_id=str(user.clinic_id), full_name=user.full_name, email=user.email, role=user.role)


@router.post("/change-password", status_code=204)
async def change_password(payload: PasswordChangeRequest, db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = utcnow()
    query = select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
    if user.current_session_id:
        query = query.where(RefreshToken.session_id != user.current_session_id)
    rows = (await db.scalars(query.with_for_update())).all()
    for row in rows:
        row.revoked_at = utcnow()
    await db.commit()

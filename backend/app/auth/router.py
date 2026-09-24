from pydantic import BaseModel, EmailStr, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import authenticate, create_access_token, get_current_user, hash_password, issue_refresh_token, rotate_refresh_token
from app.database import Clinic, User, get_db

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
    refresh = await issue_refresh_token(db, user)
    await db.commit()
    return TokenResponse(access_token=create_access_token(user), refresh_token=refresh)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await authenticate(db, payload.email, payload.password)
    if not user:
        raise HTTPException(401, "Invalid email or password")
    refresh = await issue_refresh_token(db, user)
    await db.commit()
    return TokenResponse(access_token=create_access_token(user), refresh_token=refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await rotate_refresh_token(db, payload.refresh_token)
    new_refresh = await issue_refresh_token(db, user)
    await db.commit()
    return TokenResponse(access_token=create_access_token(user), refresh_token=new_refresh)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=str(user.id), clinic_id=str(user.clinic_id), full_name=user.full_name, email=user.email, role=user.role)

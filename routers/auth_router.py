from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from core.database import get_db
from dependencies.auth_dependency import (
    get_current_user,
    get_optional_current_user,
    has_role,
)
from models.user_model import Role, User, UserRole
from schemas.user_schema import UserCreate, UserResponse, TokenResponse
from core.config import settings
from core.security import create_access_token
from services.auth_service import register_user, authenticate_user
from core.rate_limit import enforce_login_rate_limit

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    user_data: UserCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User | None, Depends(get_optional_current_user)
    ],
):
    if (
        user_data.role != UserRole.USER.value
        and (
            current_user is None
            or not has_role(current_user, UserRole.ADMIN)
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can register users with a non-user role",
        )
    if db.scalar(select(Role.id).where(Role.name == user_data.role)) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Role does not exist",
        )
    try:
        return register_user(db, user_data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")

@router.post(
    "/login",
    response_model=TokenResponse,
    description="Log in with the registered email address in the `username` field and the account password.",
)
def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
):
    enforce_login_rate_limit(request)
    user = authenticate_user(db, form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password, or account inactive",
            headers={"WWW-Authenticate": "Bearer"})
    token = create_access_token(user.id)
    return TokenResponse(access_token=token, expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)

@router.get("/me", response_model=UserResponse)
def get_my_profile(current_user: Annotated[User, Depends(get_current_user)]):
    return current_user
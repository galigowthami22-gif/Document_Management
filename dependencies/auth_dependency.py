from typing import Annotated
from jose import jwt
from jose.exceptions import JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from core.database import get_db
from models.user_model import User, UserRole
from core.security import decode_access_token
from collections.abc import Callable

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
optional_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)


def _get_user_for_token(token: str, db: Session) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token",
        headers={"WWW-Authenticate": "Bearer"})

    try:
        payload = decode_access_token(token)
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject.isdecimal():
            raise credentials_error
        user_id = int(subject)
    except (JWTError, ValueError):
        raise credentials_error
    user = db.scalar(select(User).where(User.id == user_id))

    if user is None or not user.is_active:
        raise credentials_error
    return user


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    return _get_user_for_token(token, db)


def get_optional_current_user(
    token: Annotated[str | None, Depends(optional_oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User | None:
    if token is None:
        return None
    return _get_user_for_token(token, db)


def require_roles(*allowed_roles: UserRole) -> Callable:
    allowed_values = {role.value for role in allowed_roles}

    def role_checker(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        assigned_roles = {role.name for role in current_user.roles}
        assigned_roles.add(current_user.role)
        if assigned_roles.isdisjoint(allowed_values):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action")
        return current_user
    return role_checker


def has_role(user: User, role: UserRole) -> bool:
    return user.role == role.value or any(
        assigned_role.name == role.value for assigned_role in user.roles
    )

require_admin = require_roles(UserRole.ADMIN)
require_manager_or_admin = require_roles(UserRole.ADMIN, UserRole.MANAGER)
require_upload_role = require_roles(UserRole.ADMIN, UserRole.MANAGER, UserRole.USER)
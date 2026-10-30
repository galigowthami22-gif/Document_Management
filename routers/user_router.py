from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.database import get_db
from dependencies.auth_dependency import require_admin
from models.user_model import Role, User, UserRole
from schemas.user_schema import UserResponse, UserRoleUpdate

router = APIRouter(prefix="/api/v1/users", tags=["User Management"])


@router.get("/roles", response_model=list[str])
def list_roles(
    db: Annotated[Session, Depends(get_db)],
) -> list[str]:
    return list(db.scalars(select(Role.name).order_by(Role.id)))


@router.get("/", response_model=list[UserResponse])
def list_users(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .order_by(User.id)
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


@router.patch(
    "/{user_id}/role",
    response_model=UserResponse,
)
def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    assigned_role = db.scalar(
        select(Role).where(Role.name == payload.role.value)
    )
    if assigned_role is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Role is not configured in the database",
        )
    if (
        user.is_active
        and (
            user.role == UserRole.ADMIN.value
            or UserRole.ADMIN.value in {role.name for role in user.roles}
        )
        and payload.role != UserRole.ADMIN
        and _active_admin_count(db) <= 1
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The last active admin cannot be demoted",
        )
    user.role = payload.role.value
    user.roles = [assigned_role]
    db.commit()
    db.refresh(user)
    return user


def _active_admin_count(db: Session) -> int:
    return db.scalar(
        select(func.count(User.id)).where(
            User.is_active.is_(True),
            (User.role == UserRole.ADMIN.value)
            | User.roles.any(Role.name == UserRole.ADMIN.value),
        )
    ) or 0

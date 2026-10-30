from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import get_db
from dependencies.auth_dependency import require_admin
from models.user_model import Role, User, UserRole
from schemas.user_schema import (
    RoleCreate,
    RoleResponse,
    RoleUpdate,
    UserRolesUpdate,
)

router = APIRouter(prefix="/api/v1/roles", tags=["Role Management"])


@router.get("/", response_model=list[RoleResponse])
def list_roles(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> list[Role]:
    return list(db.scalars(select(Role).order_by(Role.id)))


@router.post(
    "/",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_role(
    payload: RoleCreate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> Role:
    role = Role(
        name=payload.name.strip().lower(),
        description=payload.description,
    )
    db.add(role)
    try:
        db.commit()
        db.refresh(role)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Role name already exists",
        )
    return role


@router.patch("/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> Role:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one field must be provided",
        )
    if "name" in changes:
        new_name = changes["name"].strip().lower()
        if role.name in {item.value for item in UserRole} and new_name != role.name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Built-in role names cannot be changed",
            )
        changes["name"] = new_name

    old_name = role.name
    for field, value in changes.items():
        setattr(role, field, value)
    if "name" in changes and role.name != old_name:
        db.execute(
            update(User)
            .where(User.role == old_name)
            .values(role=role.name)
        )
    try:
        db.commit()
        db.refresh(role)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Role name already exists",
        )
    return role


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> None:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )
    if role.name in {item.value for item in UserRole}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Built-in roles cannot be deleted",
        )
    is_assigned = db.scalar(
        select(User.id)
        .where(
            (User.role == role.name) | User.roles.any(Role.id == role.id)
        )
        .limit(1)
    )
    if is_assigned is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Role is assigned to users and cannot be deleted",
        )
    db.delete(role)
    db.commit()


@router.put(
    "/users/{user_id}",
    response_model=list[RoleResponse],
)
def update_user_roles(
    user_id: int,
    payload: UserRolesUpdate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> list[Role]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    roles_by_id = {
        role.id: role
        for role in db.scalars(select(Role).where(Role.id.in_(payload.role_ids)))
    }
    if len(roles_by_id) != len(payload.role_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more roles were not found",
        )
    roles = [roles_by_id[role_id] for role_id in payload.role_ids]

    has_admin = (
        user.role == UserRole.ADMIN.value
        or UserRole.ADMIN.value in {role.name for role in user.roles}
    )
    will_have_admin = UserRole.ADMIN.value in {role.name for role in roles}
    if (
        user.is_active
        and has_admin
        and not will_have_admin
        and _active_admin_count(db) <= 1
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The last active admin cannot be demoted",
        )

    user.roles = roles
    user.role = roles[0].name
    db.commit()
    return roles


@router.get(
    "/users/{user_id}",
    response_model=list[RoleResponse],
)
def get_user_roles(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> list[Role]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user.roles


def _active_admin_count(db: Session) -> int:
    return db.scalar(
        select(func.count(User.id)).where(
            User.is_active.is_(True),
            (User.role == UserRole.ADMIN.value)
            | User.roles.any(Role.name == UserRole.ADMIN.value),
        )
    ) or 0

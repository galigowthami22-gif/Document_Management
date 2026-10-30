from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from models.user_model import UserRole

class UserCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(
        default=UserRole.USER.value,
        min_length=2,
        max_length=20,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )

class UserResponse(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    role: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class UserRoleUpdate(BaseModel):
    role: UserRole

class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=20, pattern=r"^[a-z][a-z0-9_-]*$")
    description: str | None = Field(default=None, max_length=255)

class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=20, pattern=r"^[a-z][a-z0-9_-]*$")
    description: str | None = Field(default=None, max_length=255)

class RoleResponse(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class UserRolesUpdate(BaseModel):
    role_ids: list[int] = Field(min_length=1)

    @field_validator("role_ids")
    @classmethod
    def role_ids_must_be_unique(cls, role_ids: list[int]) -> list[int]:
        if len(role_ids) != len(set(role_ids)):
            raise ValueError("Role IDs must be unique")
        return role_ids

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
from sqlalchemy import select
from sqlalchemy.orm import Session
from models.user_model import Role, User
from schemas.user_schema import UserCreate
from core.security import hash_password, verify_password

def register_user(db: Session, user_data: UserCreate) -> User:
    email = str(user_data.email).strip().lower()
    existing_user = db.scalar(select(User).where(User.email == email))

    if existing_user:
        raise ValueError("Email is already registered")

    user_role = db.scalar(select(Role).where(Role.name == user_data.role))
    if user_role is None:
        raise ValueError(f"Role '{user_data.role}' does not exist")

    user = User(
        full_name=user_data.full_name.strip(),
        email=email,
        hashed_password=hash_password(user_data.password),
        role=user_role.name,
        roles=[user_role])

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    except Exception:
        db.rollback()
        raise

def authenticate_user(db: Session, email: str, password: str) -> User | None:
    normalized_email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized_email))

    if user is None:
        return None

    if not verify_password(password, user.hashed_password):
        return None

    if not user.is_active:
        return None
    
    return user
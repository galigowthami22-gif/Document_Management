import argparse

from sqlalchemy import select

from core.database import SessionLocal
from models.user_model import Role, User, UserRole


def promote_user_to_admin(email: str) -> None:
    normalized_email = email.strip().lower()
    with SessionLocal() as db:
        user = db.scalar(
            select(User).where(User.email == normalized_email)
        )
        if user is None:
            raise ValueError(f"No user found with email {normalized_email}")
        admin_role = db.scalar(
            select(Role).where(Role.name == UserRole.ADMIN.value)
        )
        if admin_role is None:
            raise RuntimeError(
                "Administrator role is missing; apply database migrations"
            )
        if admin_role not in user.roles:
            user.roles.append(admin_role)
        user.role = UserRole.ADMIN.value
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote an existing user to administrator."
    )
    parser.add_argument("email", help="Email of the existing user")
    args = parser.parse_args()

    try:
        promote_user_to_admin(args.email)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Administrator role granted to {args.email.strip().lower()}")


if __name__ == "__main__":
    main()

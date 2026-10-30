from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core import audit, rate_limit
from core.config import settings
from core.database import Base, get_db
from core.security import create_access_token
from dependencies.auth_dependency import get_current_user
from main import app
from models.document_model import Document, DocumentShare
from models.user_model import Role, User, UserRole
from scripts import promote_user_to_admin


@pytest.fixture
def rbac_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, Any]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    owner_file = tmp_path / "owner.pdf"
    manager_file = tmp_path / "manager.pdf"
    owner_file.write_bytes(b"%PDF-1.7 owner test")
    manager_file.write_bytes(b"%PDF-1.7 manager test")

    with session_factory() as db:
        roles = {
            role.value: Role(name=role.value)
            for role in UserRole
        }
        db.add_all(roles.values())
        db.flush()
        users = {
            role.value: User(
                full_name=role.value.title(),
                email=f"{role.value}@example.com",
                hashed_password="unused",
                role=role.value,
                roles=[roles[role.value]],
            )
            for role in UserRole
        }
        db.add_all(users.values())
        db.flush()

        documents = {
            "owner": Document(
                original_filename="owner.pdf",
                stored_filename="owner.pdf",
                content_type="application/pdf",
                file_size=18,
                storage_path=str(owner_file),
                owner_id=users[UserRole.USER.value].id,
            ),
            "manager": Document(
                original_filename="manager.pdf",
                stored_filename="manager.pdf",
                content_type="application/pdf",
                file_size=20,
                storage_path=str(manager_file),
                owner_id=users[UserRole.MANAGER.value].id,
            ),
        }
        db.add_all(documents.values())
        db.flush()
        db.add_all(
            [
                DocumentShare(
                    document_id=documents["owner"].id,
                    shared_by_user_id=users[UserRole.USER.value].id,
                    shared_with_user_id=users[UserRole.VIEWER.value].id,
                    can_view=True,
                    can_download=False,
                ),
                DocumentShare(
                    document_id=documents["manager"].id,
                    shared_by_user_id=users[UserRole.MANAGER.value].id,
                    shared_with_user_id=users[UserRole.VIEWER.value].id,
                    can_view=True,
                    can_download=True,
                ),
            ]
        )
        db.commit()
        document_ids = {
            key: value.id for key, value in documents.items()
        }

    acting_user: dict[str, User] = {
        "user": users[UserRole.USER.value],
    }

    def override_db() -> Iterator[Session]:
        with session_factory() as db:
            yield db

    def override_current_user() -> User:
        return acting_user["user"]

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_current_user
    monkeypatch.setattr(audit, "SessionLocal", session_factory)

    try:
        with TestClient(app) as client:
            yield {
                "client": client,
                "users": users,
                "documents": document_ids,
                "set_role": lambda role: acting_user.update(
                    user=users[role]
                ),
                "session_factory": session_factory,
            }
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        (UserRole.ADMIN.value, 200),
        (UserRole.MANAGER.value, 403),
        (UserRole.USER.value, 403),
        (UserRole.VIEWER.value, 403),
    ],
)
def test_admin_route_enforces_roles(
    rbac_client: dict[str, Any],
    role: str,
    expected_status: int,
) -> None:
    rbac_client["set_role"](role)
    response = rbac_client["client"].get(
        "/api/v1/documents/admin-only"
    )
    assert response.status_code == expected_status
    if expected_status == 201:
        assert response.json()["original_filename"] == "upload.pdf"
        assert any(
            path.read_bytes() == b"%PDF-1.7 test upload"
            for path in settings.UPLOAD_DIR.iterdir()
            if path.is_file()
        )


def test_document_routes_require_authentication(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    current_user_override = app.dependency_overrides.pop(
        get_current_user
    )
    try:
        response = client.get("/api/v1/documents")
    finally:
        app.dependency_overrides[get_current_user] = (
            current_user_override
        )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        (UserRole.ADMIN.value, 201),
        (UserRole.MANAGER.value, 201),
        (UserRole.USER.value, 201),
        (UserRole.VIEWER.value, 403),
    ],
)
def test_upload_permissions_follow_roles(
    rbac_client: dict[str, Any],
    role: str,
    expected_status: int,
) -> None:
    rbac_client["set_role"](role)
    response = rbac_client["client"].post(
        "/api/v1/documents/upload",
        files={
            "file": (
                "upload.pdf",
                b"%PDF-1.7 test upload",
                "application/pdf",
            )
        },
    )
    assert response.status_code == expected_status


def test_listing_scopes_documents_by_role_and_shares(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    documents = rbac_client["documents"]

    rbac_client["set_role"](UserRole.USER.value)
    owner_listing = client.get("/api/v1/documents").json()
    assert [item["id"] for item in owner_listing["items"]] == [
        documents["owner"]
    ]

    rbac_client["set_role"](UserRole.VIEWER.value)
    viewer_listing = client.get("/api/v1/documents").json()
    assert {item["id"] for item in viewer_listing["items"]} == set(
        documents.values()
    )

    rbac_client["set_role"](UserRole.ADMIN.value)
    admin_listing = client.get("/api/v1/documents").json()
    assert {item["id"] for item in admin_listing["items"]} == set(
        documents.values()
    )


def test_user_management_routes_require_admin(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    rbac_client["set_role"](UserRole.USER.value)

    for path in (
        "/api/v1/users/",
        f"/api/v1/users/{rbac_client['users'][UserRole.ADMIN.value].id}",
        "/api/v1/roles/",
        f"/api/v1/roles/users/{rbac_client['users'][UserRole.ADMIN.value].id}",
    ):
        response = client.get(path)
        assert response.status_code == 403


def test_admin_can_list_roles_and_paginated_users(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    rbac_client["set_role"](UserRole.ADMIN.value)

    roles_response = client.get("/api/v1/users/roles")
    assert roles_response.status_code == 200
    assert roles_response.json() == [role.value for role in UserRole]

    users_response = client.get("/api/v1/users/?offset=1&limit=2")
    assert users_response.status_code == 200
    assert [user["id"] for user in users_response.json()] == [
        rbac_client["users"][UserRole.MANAGER.value].id,
        rbac_client["users"][UserRole.USER.value].id,
    ]


def test_admin_can_get_and_update_user_role(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    rbac_client["set_role"](UserRole.ADMIN.value)
    target_user = rbac_client["users"][UserRole.USER.value]

    get_response = client.get(f"/api/v1/users/{target_user.id}")
    assert get_response.status_code == 200
    assert get_response.json()["role"] == UserRole.USER.value

    update_response = client.patch(
        f"/api/v1/users/{target_user.id}/role",
        json={"role": UserRole.VIEWER.value},
    )
    assert update_response.status_code == 200
    assert update_response.json()["role"] == UserRole.VIEWER.value


def test_admin_cannot_demote_last_active_admin(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    admin = rbac_client["users"][UserRole.ADMIN.value]
    rbac_client["set_role"](UserRole.ADMIN.value)

    response = client.patch(
        f"/api/v1/users/{admin.id}/role",
        json={"role": UserRole.MANAGER.value},
    )
    assert response.status_code == 409


def test_roles_are_publicly_listed(
    rbac_client: dict[str, Any],
) -> None:
    response = rbac_client["client"].get("/api/v1/users/roles")

    assert response.status_code == 200
    assert response.json() == [role.value for role in UserRole]


def test_registration_accepts_user_role(
    rbac_client: dict[str, Any],
) -> None:
    response = rbac_client["client"].post(
        "/api/v1/auth/register",
        json={
            "full_name": "New User",
            "email": "new-user@example.com",
            "password": "secure-password-123",
            "role": UserRole.USER.value,
        },
    )

    assert response.status_code == 201
    assert response.json()["role"] == UserRole.USER.value


@pytest.mark.parametrize(
    "role",
    [UserRole.ADMIN.value, UserRole.MANAGER.value, UserRole.VIEWER.value],
)
def test_registration_rejects_privileged_or_non_user_roles(
    rbac_client: dict[str, Any],
    role: str,
) -> None:
    response = rbac_client["client"].post(
        "/api/v1/auth/register",
        json={
            "full_name": "New User",
            "email": f"new-{role}@example.com",
            "password": "secure-password-123",
            "role": role,
        },
    )

    assert response.status_code == 403


def test_admin_can_register_user_with_admin_role(
    rbac_client: dict[str, Any],
) -> None:
    admin = rbac_client["users"][UserRole.ADMIN.value]
    response = rbac_client["client"].post(
        "/api/v1/auth/register",
        headers={"Authorization": f"Bearer {create_access_token(admin.id)}"},
        json={
            "full_name": "New Admin",
            "email": "new-admin@example.com",
            "password": "secure-password-123",
            "role": UserRole.ADMIN.value,
        },
    )

    assert response.status_code == 201
    assert response.json()["role"] == UserRole.ADMIN.value


def test_authenticated_non_admin_cannot_register_admin(
    rbac_client: dict[str, Any],
) -> None:
    regular_user = rbac_client["users"][UserRole.USER.value]
    response = rbac_client["client"].post(
        "/api/v1/auth/register",
        headers={
            "Authorization": f"Bearer {create_access_token(regular_user.id)}"
        },
        json={
            "full_name": "Unauthorized Admin",
            "email": "unauthorized-admin@example.com",
            "password": "secure-password-123",
            "role": UserRole.ADMIN.value,
        },
    )

    assert response.status_code == 403


def test_document_access_requires_ownership_or_view_share(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    documents = rbac_client["documents"]

    rbac_client["set_role"](UserRole.MANAGER.value)
    assert client.get(
        f"/api/v1/documents/{documents['owner']}"
    ).status_code == 404

    rbac_client["set_role"](UserRole.VIEWER.value)
    assert client.get(
        f"/api/v1/documents/{documents['owner']}"
    ).status_code == 200


def test_download_requires_explicit_share_download_permission(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    documents = rbac_client["documents"]
    rbac_client["set_role"](UserRole.VIEWER.value)

    denied = client.get(
        f"/api/v1/documents/{documents['owner']}/download"
    )
    assert denied.status_code == 403

    allowed = client.get(
        f"/api/v1/documents/{documents['manager']}/download"
    )
    assert allowed.status_code == 200
    assert allowed.content.startswith(b"%PDF-1.7")


def test_only_owner_or_admin_can_share_and_delete_documents(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    document_id = rbac_client["documents"]["owner"]
    manager_id = rbac_client["users"][UserRole.MANAGER.value].id

    rbac_client["set_role"](UserRole.MANAGER.value)
    assert client.post(
        f"/api/v1/documents/{document_id}/shares",
        json={"recipient_user_id": manager_id},
    ).status_code == 404
    assert client.delete(
        f"/api/v1/documents/{document_id}"
    ).status_code == 404

    rbac_client["set_role"](UserRole.USER.value)
    created = client.post(
        f"/api/v1/documents/{document_id}/shares",
        json={
            "recipient_user_id": manager_id,
            "can_view": True,
            "can_download": True,
        },
    )
    assert created.status_code == 201

    rbac_client["set_role"](UserRole.MANAGER.value)
    assert client.get(
        f"/api/v1/documents/{document_id}"
    ).status_code == 200
    assert client.get(
        f"/api/v1/documents/{document_id}/download"
    ).status_code == 200

    rbac_client["set_role"](UserRole.USER.value)
    deleted = client.delete(f"/api/v1/documents/{document_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"message": "Document deleted successfully"}


def test_admin_can_assign_roles_but_last_admin_cannot_be_demoted(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    users = rbac_client["users"]
    rbac_client["set_role"](UserRole.USER.value)
    denied = client.patch(
        f"/api/v1/users/{users[UserRole.VIEWER.value].id}/role",
        json={"role": UserRole.MANAGER.value},
    )
    assert denied.status_code == 403

    rbac_client["set_role"](UserRole.ADMIN.value)
    updated = client.patch(
        f"/api/v1/users/{users[UserRole.VIEWER.value].id}/role",
        json={"role": UserRole.MANAGER.value},
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == UserRole.MANAGER.value

    demote_last_admin = client.patch(
        f"/api/v1/users/{users[UserRole.ADMIN.value].id}/role",
        json={"role": UserRole.USER.value},
    )
    assert demote_last_admin.status_code == 409


def test_admin_can_manage_roles_and_assign_multiple_roles(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    users = rbac_client["users"]
    rbac_client["set_role"](UserRole.ADMIN.value)

    created = client.post(
        "/api/v1/roles/",
        json={"name": "auditor", "description": "Read-only auditor"},
    )
    assert created.status_code == 201
    role = created.json()

    listed = client.get("/api/v1/roles/")
    assert listed.status_code == 200
    assert any(item["name"] == "auditor" for item in listed.json())

    updated = client.patch(
        f"/api/v1/roles/{role['id']}",
        json={"description": "Compliance auditor"},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Compliance auditor"

    user_id = users[UserRole.USER.value].id
    assigned = client.put(
        f"/api/v1/roles/users/{user_id}",
        json={
            "role_ids": [
                users[UserRole.MANAGER.value].roles[0].id,
                role["id"],
            ]
        },
    )
    assert assigned.status_code == 200
    assert [item["name"] for item in assigned.json()] == [
        UserRole.MANAGER.value,
        "auditor",
    ]

    fetched = client.get(f"/api/v1/roles/users/{user_id}")
    assert fetched.status_code == 200
    assert [item["name"] for item in fetched.json()] == [
        UserRole.MANAGER.value,
        "auditor",
    ]

    assert client.delete(f"/api/v1/roles/{role['id']}").status_code == 409
    assert client.put(
        f"/api/v1/roles/users/{user_id}",
        json={"role_ids": [users[UserRole.USER.value].roles[0].id]},
    ).status_code == 200
    assert client.delete(f"/api/v1/roles/{role['id']}").status_code == 204


def test_role_assignment_cannot_remove_last_active_admin(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]
    users = rbac_client["users"]
    rbac_client["set_role"](UserRole.ADMIN.value)

    response = client.put(
        f"/api/v1/roles/users/{users[UserRole.ADMIN.value].id}",
        json={"role_ids": [users[UserRole.USER.value].roles[0].id]},
    )

    assert response.status_code == 409


def test_operator_can_bootstrap_initial_admin(
    rbac_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        promote_user_to_admin,
        "SessionLocal",
        rbac_client["session_factory"],
    )
    promote_user_to_admin.promote_user_to_admin(" viewer@example.com ")

    with rbac_client["session_factory"]() as db:
        user = db.scalar(
            select(User).where(User.email == "viewer@example.com")
        )
        assert user is not None
        assert user.role == UserRole.ADMIN.value


def test_registration_login_and_profile_round_trip(
    rbac_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rate_limit, "_client", lambda: None)
    rate_limit._local_attempts.clear()
    client = rbac_client["client"]
    credentials = {
        "full_name": "Integration User",
        "email": "integration@example.com",
        "password": "secure-password-123",
    }

    registered = client.post("/api/v1/auth/register", json=credentials)
    assert registered.status_code == 201

    invalid_login = client.post(
        "/api/v1/auth/login",
        data={"username": credentials["email"], "password": "incorrect-password"},
    )
    assert invalid_login.status_code == 401
    assert invalid_login.headers["WWW-Authenticate"] == "Bearer"

    login = client.post(
        "/api/v1/auth/login",
        data={
            "username": credentials["email"],
            "password": credentials["password"],
        },
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    current_user_override = app.dependency_overrides.pop(get_current_user)
    try:
        profile = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides[get_current_user] = current_user_override
    assert profile.status_code == 200
    assert profile.json()["email"] == credentials["email"]


def test_invalid_registration_and_bearer_token_are_rejected(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]

    invalid_registration = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Invalid User",
            "email": "invalid@example.com",
            "password": "short",
        },
    )
    assert invalid_registration.status_code == 422
    assert invalid_registration.json()["error"]["code"] == "validation_error"

    current_user_override = app.dependency_overrides.pop(get_current_user)
    try:
        invalid_token = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer not-a-valid-token"},
        )
    finally:
        app.dependency_overrides[get_current_user] = current_user_override
    assert invalid_token.status_code == 401


def test_upload_rejects_unsupported_and_mismatched_files(
    rbac_client: dict[str, Any],
) -> None:
    client = rbac_client["client"]

    unsupported = client.post(
        "/api/v1/documents/upload",
        files={"file": ("malware.exe", b"not a supported file", "application/octet-stream")},
    )
    assert unsupported.status_code == 415

    mismatched = client.post(
        "/api/v1/documents/upload",
        files={"file": ("invalid.pdf", b"not a PDF", "application/pdf")},
    )
    assert mismatched.status_code == 415

    assert not any(
        path.name.startswith(".") or path.suffix == ".tmp"
        for path in settings.UPLOAD_DIR.iterdir()
    )


def test_upload_rejects_empty_and_oversized_files(
    rbac_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = rbac_client["client"]
    monkeypatch.setattr(settings, "MAX_FILE_SIZE", 8)

    empty = client.post(
        "/api/v1/documents/upload",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert empty.status_code == 400

    oversized = client.post(
        "/api/v1/documents/upload",
        files={"file": ("large.pdf", b"%PDF-1.7 too large", "application/pdf")},
    )
    assert oversized.status_code == 413
    assert not any(
        path.name.startswith(".") or path.suffix == ".tmp"
        for path in settings.UPLOAD_DIR.iterdir()
    )


def test_openapi_documents_supported_upload_request(
    rbac_client: dict[str, Any],
) -> None:
    schema = rbac_client["client"].get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/documents/upload"]["post"]
    request_schema = operation["requestBody"]["content"]["multipart/form-data"][
        "schema"
    ]
    component = schema["components"]["schemas"][request_schema["$ref"].split("/")[-1]]

    assert component["properties"]["file"]["contentMediaType"] == (
        "application/octet-stream"
    )
    assert "Select a PDF, TXT, PNG, JPG/JPEG, or DOCX" in component[
        "properties"
    ]["file"]["description"]
    assert all(
        extension in operation["description"]
        for extension in ("PDF", "TXT", "PNG", "JPG/JPEG", "DOCX")
    )


def test_end_to_end_register_authenticate_upload_list_and_download(
    rbac_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rate_limit, "_client", lambda: None)
    rate_limit._local_attempts.clear()
    current_user_override = app.dependency_overrides.pop(get_current_user)
    client = rbac_client["client"]
    try:
        credentials = {
            "full_name": "End-to-End User",
            "email": "end-to-end@example.com",
            "password": "secure-password-123",
        }
        registered = client.post("/api/v1/auth/register", json=credentials)
        assert registered.status_code == 201

        login = client.post(
            "/api/v1/auth/login",
            data={
                "username": credentials["email"],
                "password": credentials["password"],
            },
        )
        assert login.status_code == 200
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}"
        }

        uploaded = client.post(
            "/api/v1/documents/upload",
            headers=headers,
            files={
                "file": (
                    "end-to-end.pdf",
                    b"%PDF-1.7 end-to-end test",
                    "application/pdf",
                )
            },
        )
        assert uploaded.status_code == 201
        document_id = uploaded.json()["id"]

        listing = client.get("/api/v1/documents", headers=headers)
        assert listing.status_code == 200
        assert document_id in [item["id"] for item in listing.json()["items"]]

        download = client.get(
            f"/api/v1/documents/{document_id}/download",
            headers=headers,
        )
        assert download.status_code == 200
        assert download.content == b"%PDF-1.7 end-to-end test"
    finally:
        app.dependency_overrides[get_current_user] = current_user_override

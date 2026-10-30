# Document Management API

A FastAPI service for user authentication, role-based document access, file uploads, sharing, and audit events. The API uses MySQL for persistent data, Redis for optional caching and distributed login rate limiting, and local container storage for uploaded files.

## Run with Docker Compose

1. Copy `.env.docker.example` to `.env` (do not overwrite an existing environment file).
2. Replace the three password/secret placeholders with distinct random hex values. Generate a value with:

   ```powershell
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

   Never commit `.env`; keep production secrets in your deployment platform's secret manager.
3. Start the stack:

   ```powershell
   docker compose up --build
   ```

   Compose waits for MySQL and Redis health checks. The API container runs `alembic upgrade head` before starting Uvicorn and reports healthy at `http://localhost:8000/`.
4. Open interactive API docs at `http://localhost:8000/docs` or the OpenAPI schema at `http://localhost:8000/openapi.json`.

Uploaded files and MySQL data persist in named Docker volumes. To stop the services without deleting data, run `docker compose down`. Removing volumes is destructive and permanently deletes their contents.

## Local development

Create and activate a virtual environment, install dependencies, and configure `.env` with a reachable MySQL database, a strong `JWT_SECRET_KEY`, and optional Redis URL. Then run:

```powershell
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn main:app --reload
```

The API expects a database URL such as `mysql+pymysql://user:password@localhost:3306/document_management`. Do not reuse the Compose hostname `db` when running the API directly on the host.

## Tests

The pytest suite uses an isolated in-memory SQLite database and temporary upload directories; it does not require MySQL or Redis:

```powershell
python -m pytest -q
```

Coverage includes authentication and token validation, registration input validation, upload success and rejected file cases, unauthorized access, role-based permissions, sharing, and the OpenAPI upload contract.

## Authentication and permissions

- Register regular accounts with `POST /api/v1/auth/register`.
- Obtain a bearer token with `POST /api/v1/auth/login` using form fields `username` (the registered email) and `password`.
- Send the returned token as `Authorization: Bearer <token>`.
- Use `GET /api/v1/auth/me` to verify the authenticated account.
- Admin-only operations and document upload role rules are documented per operation in Swagger UI.

Document uploads accept PDF, TXT, PNG, JPG/JPEG, and DOCX files through multipart field `file`. File size and count limits are configurable in `core/config.py`.

## Database migrations

Alembic migrations run automatically on container startup. For a manually managed database, use `alembic upgrade head` to apply the single migration head. The role migration includes a data migration and therefore must run online against the target database.

## Project layout

- `main.py` — FastAPI app, request logging, and standardized error responses
- `routers/` — authentication, document, sharing, user, and role endpoints
- `core/` — configuration, database session, security, cache, rate limit, and audit logic
- `models/`, `schemas/`, `services/` — persistence, API contracts, and business logic
- `alembic/` — database schema migrations
- `tests/` — pytest integration and permission tests

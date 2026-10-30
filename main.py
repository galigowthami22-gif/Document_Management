import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.audit import get_actor_id, record_audit_event
from routers.auth_router import router as auth_router
from routers.document_router import router as document_router
from routers.doc_sharing_router import router as doc_sharing_router
from routers.role_router import router as role_router
from routers.user_router import router as user_router

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Document Management & Secure File Sharing",
    description="Authentication and user management APIs",
    version="1.0.0")


@app.middleware("http")
async def request_logging_and_audit(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.exception(
            "HTTP request failed request_id=%s method=%s path=%s duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            duration_ms,
        )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            record_audit_event(
                request_id=request_id,
                actor_id=get_actor_id(request.headers.get("Authorization")),
                method=request.method,
                path=request.url.path,
                status_code=500,
                client_ip=request.client.host if request.client else None,
            )
        raise
    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "HTTP request completed request_id=%s method=%s path=%s status=%s duration_ms=%s",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        record_audit_event(
            request_id=request_id,
            actor_id=get_actor_id(request.headers.get("Authorization")),
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            client_ip=request.client.host if request.client else None,
        )
    return response


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details=None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content=jsonable_encoder({
            "error": {"code": code, "message": message, "details": details},
            "request_id": getattr(request.state, "request_id", None),
        }),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    detail = exc.detail
    message = detail if isinstance(detail, str) else "Request failed"
    details = None if isinstance(detail, str) else detail
    return _error_response(
        request,
        status_code=exc.status_code,
        code=f"http_{exc.status_code}",
        message=message,
        details=details,
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _error_response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=exc.errors(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    logger.exception("Unhandled request error", exc_info=exc)
    return _error_response(
        request,
        status_code=500,
        code="internal_server_error",
        message="An unexpected error occurred",
    )


app.include_router(auth_router)
app.include_router(document_router)
app.include_router(doc_sharing_router)
app.include_router(user_router)
app.include_router(role_router)

@app.get("/", tags=["Health"])
def health_check():
    return {"message": "Document Management API is running"}
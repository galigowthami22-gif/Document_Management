import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from core import rate_limit
from core.config import settings
from main import app


def test_http_errors_use_standard_response_envelope() -> None:
    with TestClient(app) as client:
        response = client.get("/route-that-does-not-exist")

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert response.json()["error"] == {
        "code": "http_404",
        "message": "Not Found",
        "details": None,
    }


def test_login_rate_limit_blocks_requests_after_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": ("192.0.2.10", 12345),
        }
    )
    attempts_key = "rate-limit:login:192.0.2.10"
    monkeypatch.setattr(rate_limit, "_client", lambda: None)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_COUNT", 1)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(rate_limit, "_local_attempts", {attempts_key: []})

    rate_limit.enforce_login_rate_limit(request)
    with pytest.raises(HTTPException) as exc_info:
        rate_limit.enforce_login_rate_limit(request)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}

import logging

from jose import jwt
from jose.exceptions import JWTError

from core.database import SessionLocal
from core.security import decode_access_token
from models.audit_model import AuditEvent

logger = logging.getLogger(__name__)


def get_actor_id(authorization: str | None) -> int | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        subject = decode_access_token(authorization[7:]).get("sub")
        return int(subject) if isinstance(subject, str) and subject.isdecimal() else None
    except (JWTError, ValueError):
        return None


def record_audit_event(
    *,
    request_id: str,
    actor_id: int | None,
    method: str,
    path: str,
    status_code: int,
    client_ip: str | None,
) -> None:
    try:
        with SessionLocal() as db:
            db.add(
                AuditEvent(
                    request_id=request_id,
                    actor_id=actor_id,
                    method=method,
                    path=path,
                    status_code=status_code,
                    client_ip=client_ip,
                )
            )
            db.commit()
    except Exception:
        logger.exception("Failed to persist request audit event")

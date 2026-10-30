import logging
import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from core.cache import _client
from core.config import settings

logger = logging.getLogger(__name__)
_local_attempts: dict[str, list[float]] = defaultdict(list)
_local_lock = threading.Lock()


def enforce_login_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    key = f"rate-limit:login:{client_ip}"
    limit = settings.LOGIN_RATE_LIMIT_COUNT
    window = settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
    redis_client = _client()

    if redis_client is not None:
        try:
            with redis_client.pipeline() as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, window)
                attempts, _ = pipeline.execute()
            if attempts > limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many login attempts. Please try again later.",
                    headers={"Retry-After": str(window)},
                )
            return
        except HTTPException:
            raise
        except RedisError:
            logger.exception(
                "Redis login rate limiter unavailable; using process-local limits"
            )

    now = time.monotonic()
    with _local_lock:
        attempts = [
            attempt
            for attempt in _local_attempts[key]
            if now - attempt < window
        ]
        if len(attempts) >= limit:
            _local_attempts[key] = attempts
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Please try again later.",
                headers={"Retry-After": str(window)},
            )
        attempts.append(now)
        _local_attempts[key] = attempts

import json
import logging
from typing import Any
from redis import Redis
from redis.exceptions import RedisError
from core.config import settings

logger = logging.getLogger(__name__)
_redis_client: Redis | None = None


def _client() -> Redis | None:
    global _redis_client
    if not settings.REDIS_URL:
        return None
    if _redis_client is None:
        _redis_client = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return _redis_client


def get_cached_json(key: str) -> Any | None:
    client = _client()
    if client is None:
        return None
    try:
        value = client.get(key)
        return json.loads(value) if value is not None else None
    except (RedisError, json.JSONDecodeError):
        logger.exception("Redis cache read failed for key %s", key)
        return None


def set_cached_json(key: str, value: Any, ttl: int) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.set(key, json.dumps(value), ex=ttl)
    except (RedisError, TypeError, ValueError):
        logger.exception("Redis cache write failed for key %s", key)


def invalidate_document_lists() -> None:
    client = _client()
    if client is None:
        return
    try:
        keys = list(client.scan_iter(match="documents:list:*"))
        if keys:
            client.delete(*keys)
    except RedisError:
        logger.exception("Redis document-list cache invalidation failed")

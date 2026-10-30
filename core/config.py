from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    UPLOAD_DIR: Path = Path("storage/documents")
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10 MB
    MAX_FILES_PER_REQUEST: int = 5
    REDIS_URL: str | None = None
    CACHE_TTL_SECONDS: int = 60
    LOGIN_RATE_LIMIT_COUNT: int = 5
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore")

settings = Settings()
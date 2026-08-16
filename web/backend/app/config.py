"""
Application configuration using Pydantic Settings.
Reads from environment variables or .env file.
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Central Backend configuration."""

    # ── App ──────────────────────────────────────────────
    APP_NAME: str = "Digital Twin - Central API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    APP_PORT: int = 8081

    # ── JWT Authentication ───────────────────────────────
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── CORS ─────────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost,http://127.0.0.1,http://127.0.0.1:3000"

    # ── Crack Detection API ──────────────────────────────
    CRACK_API_URL: str = "http://localhost:8000"
    CRACK_API_TOKEN: str
    TWIN_API_URL: str = "http://host.docker.internal:8090"
    TWIN_API_TOKEN: str = "secure_token_CrackAPI_12345678@@"

    # ── RAGFlow Middleware API ────────────────────────────
    RAGFLOW_API_URL: str = "http://100.122.165.47:8085"
    RAGFLOW_API_TOKEN: str = "secure_token_RAGFlow_12345678@@"

    # ── Redis Cache & PubSub ─────────────────────────────
    REDIS_URL: str = "redis://:infini_rag_flow@localhost:6379/0"

    # ── RAGFlow Dashboard Config (UI Overridable) ─────────
    RAGFLOW_BASE_URL: str = "http://localhost:8088/api/v1/chats_openai/default"
    DATASET_ID: str = ""
    RAGFLOW_API_KEY: str = ""

    # ── MinIO Object Storage (S3) ────────────────────────
    MINIO_ENDPOINT: str = "http://localhost:9000"
    MINIO_ACCESS_KEY: str = "rag_flow"
    MINIO_SECRET_KEY: str = "infini_rag_flow"
    MINIO_BUCKET: str = "crack-detection"
    MINIO_PUBLIC_URL: str = "http://localhost:9000"

    MONGODB_URL: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "digital_twin"

    LOCAL_SOURCES_DIR: str = r"D:\crack_api\sources" 
    INTERNAL_AI_DATA_ROOT: str = "/data/file/sources" # Root path inside AI Container

    # ── Admin default credentials ────────────────────────
    ADMIN_EMAIL: str
    ADMIN_PASSWORD: str

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()

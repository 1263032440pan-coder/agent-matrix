"""Project Autonomous Agent — Runtime configuration."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = "127.0.0.1"
    port: int = 9100

    # Database
    db_path: str = "data/project_autonomous_agent.db"

    # Logging
    log_level: str = "INFO"
    log_file: str = "data/runtime.log"

    # Project root (for resolving relative paths)
    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent.parent

    @property
    def db_url(self) -> str:
        db_abs = self.project_root / self.db_path
        db_abs.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_abs}"

    @property
    def db_sync_url(self) -> str:
        db_abs = self.project_root / self.db_path
        db_abs.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_abs}"


settings = Settings()

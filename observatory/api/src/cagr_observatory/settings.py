"""Settings from CAGR_* environment variables or a .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAGR_", env_file=".env", extra="ignore")

    observatory_host: str = "127.0.0.1"
    observatory_port: int = 8400
    web_dist: Path = WEB_DIST
    """The built UI (npm run build in observatory/web). Not served when missing."""

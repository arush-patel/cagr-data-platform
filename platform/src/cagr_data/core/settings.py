"""Runtime settings, read from CAGR_* environment variables or a .env file."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAGR_", env_file=".env", extra="ignore")

    database_url: SecretStr | None = None
    """Connection string for the platform's database (cagr_test during the test phase)."""

    pipeline_database_url: SecretStr | None = None
    """The pipeline's login (cagr_writer); jobs and builders use it, migrations never do."""

    raw_root: Path = Path("data/raw")
    """Where original vendor files are kept: a local folder now, Azure Blob once deployed."""

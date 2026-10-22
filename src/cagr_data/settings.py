"""Runtime settings, read from the environment or a .env file (one per environment, never in git)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAGR_", env_file=".env", extra="ignore")

    # Local default matches docker-compose.yml.
    database_url: str = "postgresql://cagr:cagr@localhost:54329/cagr_dev"
    env: str = "dev"

    # Where downloaded vendor files are kept, so a rerun never downloads again.
    raw_file_root: Path = Path("data/raw")
    # Parquet lake root: a local folder now, an ADLS mount later.
    lake_root: Path = Path("data/lake")

    # NSE serves scripts that look like a browser; the API host also wants a cookie session.
    http_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    )
    http_timeout_s: float = 30.0
    http_max_retries: int = 4

    # Equity series taken from the bhavcopy, in order of preference when one ISIN has several.
    equity_series: tuple[str, ...] = ("EQ", "BE", "BZ", "SM", "ST", "SZ")


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""uv run observatory: the API and the built UI on localhost."""

from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from cagr_observatory.app import create_app
from cagr_observatory.settings import Settings

# The repository's .env, so the settings are found whichever folder the server starts from.
# Variables already set in the environment win.
REPO_ENV = Path(__file__).resolve().parents[4] / ".env"


def main() -> None:
    load_dotenv(REPO_ENV)
    settings = Settings()
    uvicorn.run(
        create_app(settings), host=settings.observatory_host, port=settings.observatory_port
    )


if __name__ == "__main__":
    main()

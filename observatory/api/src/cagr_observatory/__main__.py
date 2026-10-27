"""uv run observatory: the API and the built UI on localhost."""

import uvicorn

from cagr_observatory.app import create_app
from cagr_observatory.settings import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        create_app(settings), host=settings.observatory_host, port=settings.observatory_port
    )


if __name__ == "__main__":
    main()

"""Write the API's OpenAPI schema, which the UI's TypeScript types are generated from.

uv run python -m cagr_observatory.openapi    # then: cd observatory/web && npm run api-types
"""

import json
from pathlib import Path
from typing import Any

from cagr_observatory.app import create_app
from cagr_observatory.settings import Settings

SCHEMA = Path(__file__).resolve().parents[3] / "web" / "src" / "api" / "openapi.json"


def schema() -> dict[str, Any]:
    return create_app(Settings(web_dist=Path("/nonexistent"))).openapi()


def text() -> str:
    return json.dumps(schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    SCHEMA.write_text(text())

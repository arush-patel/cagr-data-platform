from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def nse_fixtures() -> Path:
    return FIXTURES / "nse"

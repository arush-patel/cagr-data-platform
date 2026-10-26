import zipfile
from pathlib import Path

import pytest

import cagr_data
from cagr_data.core.settings import Settings


def test_version() -> None:
    assert cagr_data.__version__ == "0.1.0"


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CAGR_RAW_ROOT", str(tmp_path))
    monkeypatch.setenv("CAGR_DATABASE_URL", "postgresql://u:p@localhost/cagr_test")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.raw_root == tmp_path
    assert settings.database_url is not None
    assert "p@" not in repr(settings)


def test_bhavcopy_fixtures_open(nse_fixtures: Path) -> None:
    zips = sorted(nse_fixtures.glob("*.zip"))
    assert len(zips) == 4
    for path in zips:
        with zipfile.ZipFile(path) as zf:
            (inner,) = zf.namelist()
            assert inner.endswith(".csv")
            assert zf.read(inner).count(b"\n") > 1

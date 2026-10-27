from datetime import date
from pathlib import Path

import pytest

from cagr_data.core.storage import LocalStorage, StorageConflictError, raw_key


def test_raw_key() -> None:
    day = date(2024, 7, 5)
    assert raw_key("nse", "nse_cm_bhavcopy_legacy", day, "cm05JUL2024bhav.csv.zip") == (
        "nse/nse_cm_bhavcopy_legacy/2024/07/cm05JUL2024bhav.csv.zip"
    )
    assert raw_key("nse", "f", day, "cm05JUL2024bhav.csv.zip", "abcdef0123456789" * 4) == (
        "nse/f/2024/07/cm05JUL2024bhav@abcdef012345.csv.zip"
    )


def test_put_is_write_once(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    uri = storage.put("nse/f/2024/07/a.csv", b"one")
    assert uri.startswith("file://")
    assert storage.get(uri) == b"one"
    assert storage.put("nse/f/2024/07/a.csv", b"one") == uri
    with pytest.raises(StorageConflictError):
        storage.put("nse/f/2024/07/a.csv", b"two")
    assert storage.get(uri) == b"one"
    assert [p.name for p in (tmp_path / "nse/f/2024/07").iterdir()] == ["a.csv"]


@pytest.mark.parametrize("key", ["", "/etc/passwd", "nse/../../x", "nse/./x"])
def test_bad_keys(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).put(key, b"x")


def test_get_stays_inside_the_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"x")
    storage = LocalStorage(tmp_path / "root")
    with pytest.raises(ValueError):
        storage.get(outside.as_uri())
    with pytest.raises(ValueError):
        storage.get("https://example.com/a")

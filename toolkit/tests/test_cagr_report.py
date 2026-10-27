import zipfile
from pathlib import Path

import polars as pl
import pytest

from cagr import report


def sheet_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        xml = z.read("xl/workbook.xml").decode()
    return [part.split('"')[0] for part in xml.split('<sheet name="')[1:]]


def shared_strings(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("xl/sharedStrings.xml").decode()


def test_the_run_sheet_comes_first(tmp_path: Path) -> None:
    out = tmp_path / "runs" / "r.xlsx"
    with report.workbook(out, "Contraction by industry", run={"Features": ["a@1", "b@2"]}) as wb:
        wb.sheet("By industry", pl.DataFrame({"industry": ["Banks"], "rate": [0.4]}))
        wb.sheet("Detail", pl.DataFrame({"x": [1]}))
    assert sheet_names(out) == ["Run", "By industry", "Detail"]
    strings = shared_strings(out)
    assert "Contraction by industry" in strings and "a@1, b@2" in strings
    assert "Toolkit version" in strings


def test_nothing_is_written_when_the_block_fails(tmp_path: Path) -> None:
    out = tmp_path / "r.xlsx"
    with pytest.raises(RuntimeError), report.workbook(out, "t") as wb:
        wb.sheet("A", pl.DataFrame({"x": [1]}))
        raise RuntimeError("boom")
    assert not out.exists()


@pytest.mark.parametrize("name", ["Run", "a" * 32, "bad/name"])
def test_bad_sheet_names(tmp_path: Path, name: str) -> None:
    wb = report.Workbook("t", {})
    with pytest.raises(ValueError):
        wb.sheet(name, pl.DataFrame({"x": [1]}))

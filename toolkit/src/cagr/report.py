"""Excel reports with a Run sheet first.

    with report.workbook("runs/contraction.xlsx", "Contraction by industry", run=info) as wb:
        wb.sheet("By industry", hits)

Every workbook opens on a Run sheet that says what made it: the title, when, the toolkit
version, and whatever the run passes in (run id, dataset recipe hash, feature and label
versions, parameters). Your sheets follow in the order you add them. The file is written when
the block ends, and not at all if the block raises.

Saving with the run in the lake and copying to SharePoint come in later slices.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import xlsxwriter

import cagr

MAX_SHEET_NAME = 31  # Excel's limit


class Workbook:
    def __init__(self, title: str, run: Mapping[str, Any]) -> None:
        self.title = title
        self.run = dict(run)
        self.sheets: dict[str, pl.DataFrame] = {}

    def sheet(self, name: str, frame: pl.DataFrame) -> None:
        """Add a sheet. Names must be unique, 31 characters at most, and not "Run"."""
        if name == "Run" or name in self.sheets:
            raise ValueError(f"sheet name {name!r} is taken")
        if len(name) > MAX_SHEET_NAME or any(c in name for c in "[]:*?/\\"):
            raise ValueError(f"{name!r} is not a valid Excel sheet name")
        self.sheets[name] = frame

    def run_rows(self) -> pl.DataFrame:
        info: dict[str, Any] = {
            "Report": self.title,
            "Created (UTC)": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "Toolkit version": cagr.__version__,
            **self.run,
        }
        return pl.DataFrame(
            {"Item": list(info), "Value": [_text(v) for v in info.values()]},
            schema={"Item": pl.String, "Value": pl.String},
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with xlsxwriter.Workbook(str(path)) as wb:
            self.run_rows().write_excel(wb, worksheet="Run", autofit=True)
            for name, frame in self.sheets.items():
                frame.write_excel(wb, worksheet=name, autofit=True)


def _text(value: Any) -> str:
    if isinstance(value, list | tuple | set):
        return ", ".join(str(v) for v in value)
    return str(value)


@contextmanager
def workbook(
    path: str | Path, title: str, *, run: Mapping[str, Any] | None = None
) -> Iterator[Workbook]:
    """Build a report; it is written to path when the block finishes without an error."""
    wb = Workbook(title, run or {})
    yield wb
    wb.write(Path(path))

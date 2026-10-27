"""Reading NSE's CSV files: every field as text, checked against the header we expect.

NSE's CSVs share a few habits: header names with stray spaces, a trailing comma on every line in
some files, CRLF line endings in others, and "-" or "" for a missing value. The parsers read the
whole file as text with these helpers and cast once, so a value that does not fit its column is
a FileShapeError rather than a silent null.
"""

import io

import polars as pl

from cagr_data.vendors.errors import FileShapeError

MISSING = ("", "-")


def header(csv: bytes) -> list[str]:
    """The header's names, stripped, without the empty name a trailing comma leaves."""
    first = csv.split(b"\n", 1)[0].decode("utf-8-sig").rstrip("\r")
    names = [n.strip() for n in first.split(",")]
    if names and names[-1] == "":
        names.pop()
    return names


def body(csv: bytes, names: list[str]) -> pl.DataFrame:
    """Every field as text, named by position; the trailing empty field, if any, is dropped."""
    frame = pl.read_csv(
        io.BytesIO(csv),
        has_header=False,
        skip_rows=1,
        infer_schema=False,
        truncate_ragged_lines=True,
    )
    if frame.height == 0:
        return pl.DataFrame(schema=dict.fromkeys(names, pl.String))
    if frame.width < len(names):
        raise FileShapeError(f"rows have {frame.width} fields, header names {len(names)}")
    extra = frame.columns[len(names) :]
    if extra and frame.select(pl.any_horizontal(pl.col(extra).is_not_null())).to_series().any():
        raise FileShapeError(f"rows have {frame.width} fields, header names {len(names)}")
    return frame.drop(extra).rename(dict(zip(frame.columns, names, strict=False)))


def text(name: str) -> pl.Expr:
    """A text field, stripped, with "" and "-" read as missing."""
    return pl.col(name).str.strip_chars().replace(list(MISSING), None)


def cast(frame: pl.DataFrame, schema: dict[str, pl.DataType]) -> pl.DataFrame:
    """Cast every column to its type, strictly."""
    try:
        return frame.select([pl.col(c).cast(t) for c, t in schema.items()])
    except pl.exceptions.InvalidOperationError as e:
        raise FileShapeError(f"a value does not fit its column: {e}") from e


def parse_date(name: str, fmt: str) -> pl.Expr:
    """A date field; a value that is present but not a date in this format raises on check."""
    return text(name).str.strptime(pl.Date, fmt, strict=False)


def check_dates_parsed(frame: pl.DataFrame, raw: pl.DataFrame, pairs: dict[str, str]) -> None:
    """Every date column that had text in the file must have parsed (column -> source column)."""
    for column, source in pairs.items():
        unparsed = frame[column].is_null() & raw.select(text(source)).to_series().is_not_null()
        if unparsed.any():
            bad = raw.filter(unparsed)[source][0]
            raise FileShapeError(f"{source} is not a date: {bad!r}")

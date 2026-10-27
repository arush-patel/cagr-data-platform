"""Errors a vendor file can raise while it is parsed. Each maps to a raw-layer check."""

from datetime import date


class VendorFileError(Exception):
    """A file we received cannot be loaded as it is."""


class FileShapeError(VendorFileError):
    """The file's columns are not the ones its format expects (check `file_shape`)."""


class TradeDateMismatchError(VendorFileError):
    """The date inside the file is not the date we asked for (check `trade_date_matches`)."""

    def __init__(self, expected: date, found: list[date]) -> None:
        super().__init__(f"expected {expected}, file holds {', '.join(map(str, found))}")
        self.expected = expected
        self.found = found

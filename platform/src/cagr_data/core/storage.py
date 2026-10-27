"""Where the original vendor files are kept (spec 0001, section 5.2).

Files are write-once: putting the same bytes under a key again is a no-op, putting different
bytes under a used key is an error. A revision is stored under a new key, never as an overwrite.
The pipeline only sees the Storage protocol, so moving from a local folder to Azure Blob is
configuration plus one class.
"""

import io
import os
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, unquote, urlsplit


class StorageConflictError(Exception):
    """A key already holds different bytes."""


class Storage(Protocol):
    def put(self, key: str, content: bytes) -> str:
        """Store content under key and return its URI."""
        ...

    def get(self, uri: str) -> bytes:
        """The bytes stored at a URI this storage returned."""
        ...

    def pack(self, key: str, members: dict[str, bytes]) -> dict[str, str]:
        """Store members as one archive under key; returns each member's URI."""
        ...

    def delete(self, uri: str) -> None:
        """Remove a loose file this storage returned. Archives are never deleted."""
        ...


def raw_key(
    vendor: str,
    feed_id: str,
    folder_date: date,
    file_name: str,
    sha256: str | None = None,
) -> str:
    """{vendor}/{feed_id}/{YYYY}/{MM}/{file name}; with sha256, '@' and its first 12 characters
    go before the first dot of the name (a revision)."""
    if sha256:
        stem, dot, rest = file_name.partition(".")
        file_name = f"{stem}@{sha256[:12]}{dot}{rest}"
    return f"{vendor}/{feed_id}/{folder_date:%Y}/{folder_date:%m}/{file_name}"


class LocalStorage:
    """Files under a local folder, addressed by file:// URIs. A file packed into an archive is
    addressed by the archive's URI with the member's name as the fragment
    (file:///.../2010/01.zip#cm04JAN2010bhav.csv.zip)."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if key.startswith("/") or any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"not a relative key: {key!r}")
        return self.root.joinpath(*parts)

    def put(self, key: str, content: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            try:
                os.link(tmp, path)  # fails if the key exists, so nothing is overwritten
            except FileExistsError:
                if path.read_bytes() != content:
                    raise StorageConflictError(f"{key} already holds different bytes") from None
        finally:
            os.unlink(tmp)
        return path.as_uri()

    def _local(self, uri: str) -> tuple[Path, str]:
        parts = urlsplit(uri)
        if parts.scheme != "file":
            raise ValueError(f"not a file URI: {uri}")
        path = Path(unquote(parts.path)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"{uri} is outside {self.root}")
        return path, unquote(parts.fragment)

    def get(self, uri: str) -> bytes:
        path, member = self._local(uri)
        if member:
            with zipfile.ZipFile(path) as zf:
                return zf.read(member)
        return path.read_bytes()

    def pack(self, key: str, members: dict[str, bytes]) -> dict[str, str]:
        """Write members into a new zip at key. Members that are already zips are stored as
        they are; the rest are deflated."""
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            for name, content in sorted(members.items()):
                packed = name.lower().endswith(".zip")
                zf.writestr(name, content,
                            zipfile.ZIP_STORED if packed else zipfile.ZIP_DEFLATED)  # fmt: skip
        uri = self.put(key, out.getvalue())
        return {name: f"{uri}#{quote(name)}" for name in members}

    def delete(self, uri: str) -> None:
        path, member = self._local(uri)
        if member:
            raise ValueError(f"{uri} is inside an archive; archives are kept")
        path.unlink(missing_ok=True)

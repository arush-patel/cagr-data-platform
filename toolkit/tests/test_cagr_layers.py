"""The toolkit's dependency rules (spec 0004, section 4), checked on its source."""

import ast
from pathlib import Path

import cagr

SRC = Path(cagr.__file__).parent
COMPUTE = {"ind", "metrics", "calendar", "splits", "fs"}


def imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_toolkit_never_imports_the_platform() -> None:
    for path in SRC.rglob("*.py"):
        assert not any(n.split(".")[0] == "cagr_data" for n in imports(path)), path.name


def test_compute_and_definitions_do_no_io() -> None:
    for name in COMPUTE:
        found = imports(SRC / f"{name}.py")
        assert not found & {"psycopg", "cagr.db", "httpx", "duckdb"}, name


def test_version() -> None:
    assert cagr.__version__ == "0.1.0"

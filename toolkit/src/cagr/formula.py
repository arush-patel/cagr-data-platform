"""Analysts' formulas: a small, safe language that compiles to the same Polars expressions as
features.

    close / rolling_max(high, 20) - 1
    rsi(14) > 70 and close > sma(close, 50)

What a formula may use:

- the bar columns open, high, low, close, volume, and numbers
- + - * / and unary minus; < <= > >= == !=; and, or, not; parentheses
- the functions in FUNCTIONS. Windows and lags are whole numbers written in the formula

What it can't do is look forward: there is no way to read a later bar, because `lag` only
accepts positive numbers and every window ends at the current bar. Anything else (attributes,
indexing, other names) is refused before anything runs.

`to_feature` turns a formula into a `cagr.fs.Feature`, with its inputs and warm-up worked out
from the formula, so an accepted formula is computed and checked like any other feature.
"""

import ast
import dataclasses
import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

from cagr import fs, ind

COLUMNS = ("open", "high", "low", "close", "volume")


class FormulaError(ValueError):
    """The formula is not valid in the formula language."""


@dataclass(frozen=True)
class _Node:
    expr: pl.Expr
    lookback: int
    """Bars before the current one this value needs (0 for a column)."""
    inputs: frozenset[str]


# name -> (argument kinds, builder, extra lookback from the window)
# kinds: "x" an expression, "n" a positive whole number
_Builder = Callable[..., pl.Expr]
FUNCTIONS: dict[str, tuple[str, _Builder, Callable[[int], int]]] = {
    "lag": ("xn", lambda x, n: x.shift(n), lambda n: n),
    "sma": ("xn", lambda x, n: ind.sma(n, x), lambda n: n - 1),
    "ema": ("xn", lambda x, n: ind.ema(n, x), lambda n: n - 1),
    "rolling_max": ("xn", lambda x, n: x.rolling_max(n), lambda n: n - 1),
    "rolling_min": ("xn", lambda x, n: x.rolling_min(n), lambda n: n - 1),
    "rolling_std": ("xn", lambda x, n: x.rolling_std(n), lambda n: n - 1),
    "change": ("xn", lambda x, n: x / x.shift(n) - 1, lambda n: n),
    "abs": ("x", lambda x: x.abs(), lambda _: 0),
    "log": ("x", lambda x: x.log(), lambda _: 0),
    "min": ("xx", lambda a, b: pl.min_horizontal(a, b), lambda _: 0),
    "max": ("xx", lambda a, b: pl.max_horizontal(a, b), lambda _: 0),
}
# indicators on the bar columns themselves: name -> (builder, columns, lookback for n)
INDICATORS: dict[str, tuple[Callable[[int], pl.Expr], tuple[str, ...], Callable[[int], int]]] = {
    "rsi": (ind.rsi, ("close",), lambda n: n),
    "atr": (ind.atr, ("high", "low", "close"), lambda n: n - 1),
    "atr_pct": (ind.atr_pct, ("high", "low", "close"), lambda n: n - 1),
    "adx": (ind.adx, ("high", "low", "close"), lambda n: 2 * n - 1),
}

_Pair = Callable[[pl.Expr, pl.Expr], pl.Expr]
_BINARY: dict[type[ast.operator], _Pair] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}
_COMPARE: dict[type[ast.cmpop], _Pair] = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


def _whole(node: ast.expr, fn: str) -> int:
    if not (isinstance(node, ast.Constant) and type(node.value) is int and node.value >= 1):
        raise FormulaError(f"{fn}: the window must be a whole number of at least 1")
    return node.value


def _compile(node: ast.expr) -> _Node:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        value = node.value
        assert isinstance(value, int | float)
        return _Node(pl.lit(float(value)), 0, frozenset())
    if isinstance(node, ast.Name):
        if node.id not in COLUMNS:
            raise FormulaError(f"unknown name {node.id!r}; columns are {', '.join(COLUMNS)}")
        return _Node(pl.col(node.id), 0, frozenset({node.id}))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.Not):
        inner = _compile(node.operand)
        expr = -inner.expr if isinstance(node.op, ast.USub) else ~inner.expr
        return _Node(expr, inner.lookback, inner.inputs)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        a, b = _compile(node.left), _compile(node.right)
        return _Node(
            _BINARY[type(node.op)](a.expr, b.expr), max(a.lookback, b.lookback), a.inputs | b.inputs
        )
    if isinstance(node, ast.Compare):
        parts = [_compile(node.left), *(_compile(c) for c in node.comparators)]
        terms = []
        for op, a, b in zip(node.ops, parts, parts[1:], strict=False):
            if type(op) not in _COMPARE:
                raise FormulaError("only < <= > >= == != compare values")
            terms.append(_COMPARE[type(op)](a.expr, b.expr))
        expr = terms[0]
        for t in terms[1:]:
            expr = expr & t
        return _Node(
            expr, max(p.lookback for p in parts), frozenset().union(*(p.inputs for p in parts))
        )
    if isinstance(node, ast.BoolOp):
        parts = [_compile(v) for v in node.values]
        expr = parts[0].expr
        for p in parts[1:]:
            expr = expr & p.expr if isinstance(node.op, ast.And) else expr | p.expr
        return _Node(
            expr, max(p.lookback for p in parts), frozenset().union(*(p.inputs for p in parts))
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        return _call(node.func.id, node.args)
    raise FormulaError(f"not allowed in a formula: {ast.unparse(node)!r}")


def _call(name: str, args: list[ast.expr]) -> _Node:
    if name in INDICATORS:
        build, columns, extra = INDICATORS[name]
        if len(args) != 1:
            raise FormulaError(f"{name} takes one window, as in {name}(14)")
        n = _whole(args[0], name)
        return _Node(build(n), extra(n), frozenset(columns))
    if name not in FUNCTIONS:
        known = sorted([*FUNCTIONS, *INDICATORS])
        raise FormulaError(f"unknown function {name!r}; available: {', '.join(known)}")
    kinds, build, extra = FUNCTIONS[name]
    if len(args) != len(kinds):
        raise FormulaError(f"{name} takes {len(kinds)} argument(s)")
    values: list[pl.Expr | int] = []
    lookback, inputs, window = 0, frozenset[str](), 0
    for kind, arg in zip(kinds, args, strict=True):
        if kind == "n":
            window = _whole(arg, name)
            values.append(window)
        else:
            sub = _compile(arg)
            values.append(sub.expr)
            lookback, inputs = max(lookback, sub.lookback), inputs | sub.inputs
    return _Node(build(*values), lookback + extra(window), inputs)


def _parse(text: str) -> _Node:
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"can't read the formula: {e.msg}") from None
    return _compile(tree.body)


def compile_formula(text: str) -> pl.Expr:
    """The Polars expression for a formula, over one listing's bars."""
    return _parse(text).expr


def to_feature(text: str, *, name: str, version: int) -> fs.Feature:
    """A feature computed by the formula, with inputs and warm-up taken from it."""
    node = _parse(text)
    if not node.inputs:
        raise FormulaError("a formula must read at least one column")
    expr = node.expr

    def formula_feature() -> pl.Expr:
        return expr

    f = fs.feature(
        name=name, version=version, inputs=sorted(node.inputs), min_bars=node.lookback + 1
    )(formula_feature)
    # the formula text is its source, so a changed formula can't reuse a version
    return dataclasses.replace(f, code_hash=hashlib.sha256(text.strip().encode()).hexdigest())

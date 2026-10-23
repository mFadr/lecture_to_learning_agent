"""Bezpečné vyhodnocení matematického výrazu v proměnné x (bez eval).

Model smí poslat jen výraz; ten se převede na AST a povolené jsou pouze čísla, x, konstanty,
aritmetické operátory a malá sada funkcí."""

from __future__ import annotations

import ast
import math
from typing import Callable

_FUNCS: dict[str, Callable[[float], float]] = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "exp": math.exp, "log": math.log,
    "ln": math.log, "log10": math.log10, "sqrt": math.sqrt, "abs": abs, "asin": math.asin,
    "acos": math.acos, "atan": math.atan, "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
}
_CONSTS = {"pi": math.pi, "e": math.e}
_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
           ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b}
_UNOPS = {ast.UAdd: lambda a: +a, ast.USub: lambda a: -a}


class ExpressionError(ValueError):
    pass


def _prepare(expr: str) -> str:
    expr = expr.strip()
    if "=" in expr:  # "y = 2x" → "2x"
        expr = expr.split("=")[-1].strip()
    expr = expr.replace("^", "**").replace("·", "*").replace("×", "*").replace("−", "-").replace(",", ".")
    if len(expr) > 200:
        raise ExpressionError("Výraz je příliš dlouhý.")
    return expr


def compile_expression(expr: str) -> Callable[[float], float]:
    src = _prepare(expr)
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Neplatný výraz „{expr}“.") from exc
    _check(tree.body)

    def f(x: float) -> float:
        return float(_eval(tree.body, x))
    return f


def _check(node: ast.AST, depth: int = 0) -> None:
    if depth > 40:
        raise ExpressionError("Výraz je příliš složitý.")
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise ExpressionError("Povolena jsou jen čísla.")
    elif isinstance(node, ast.Name):
        if node.id != "x" and node.id not in _CONSTS:
            raise ExpressionError(f"Neznámá proměnná „{node.id}“ (povolena je jen x).")
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise ExpressionError("Nepovolený operátor.")
        _check(node.left, depth + 1)
        _check(node.right, depth + 1)
    elif isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNOPS:
            raise ExpressionError("Nepovolený operátor.")
        _check(node.operand, depth + 1)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS or len(node.args) != 1 or node.keywords:
            raise ExpressionError("Nepovolená funkce.")
        _check(node.args[0], depth + 1)
    else:
        raise ExpressionError(f"Nepovolená konstrukce ({type(node).__name__}).")


def _eval(node: ast.AST, x: float) -> float:
    if isinstance(node, ast.Constant):
        return node.value  # type: ignore[return-value]
    if isinstance(node, ast.Name):
        return x if node.id == "x" else _CONSTS[node.id]
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, x), _eval(node.right, x)
        if isinstance(node.op, ast.Pow) and abs(b) > 100:
            raise OverflowError
        return _BINOPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp):
        return _UNOPS[type(node.op)](_eval(node.operand, x))
    if isinstance(node, ast.Call):
        return _FUNCS[node.func.id](_eval(node.args[0], x))  # type: ignore[union-attr]
    raise ExpressionError("Nepovolená konstrukce.")


def sample(expr: str, x_min: float, x_max: float, n: int = 400) -> tuple[list[float], list[float | None]]:
    f = compile_expression(expr)
    xs = [x_min + (x_max - x_min) * i / (n - 1) for i in range(n)]
    ys: list[float | None] = []
    for x in xs:
        try:
            y = f(x)
            ys.append(y if math.isfinite(y) and abs(y) < 1e12 else None)
        except (ValueError, ZeroDivisionError, OverflowError):
            ys.append(None)
    return xs, ys

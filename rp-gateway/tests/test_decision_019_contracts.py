from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from app.services.state_store import StateStore


APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _string_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _turn_named_parameter(argument: ast.arg) -> bool:
    name = argument.arg.casefold()
    annotation = ast.unparse(argument.annotation).casefold() if argument.annotation is not None else ""
    if annotation:
        return "turn" in annotation and ("int" in annotation or "number" in annotation or "index" in annotation)
    return name in {"turn", "current_turn", "turn_number", "turn_index", "turn_count", "turns_count"}


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []

    class ScopeVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is scope:
                self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node: ast.Lambda) -> None:
            if node is scope:
                self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            if node is scope:
                self.generic_visit(node)

        def generic_visit(self, node: ast.AST) -> None:
            nodes.append(node)
            super().generic_visit(node)

    ScopeVisitor().visit(scope)
    return nodes


def _target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {name for item in target.elts for name in _target_names(item)}
    return set()


def _contains_turn_collection(node: ast.AST) -> bool:
    return any(
        (isinstance(item, ast.Subscript) and _string_key(item.slice) == "turns")
        or (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "get"
            and bool(item.args)
            and _string_key(item.args[0]) == "turns"
        )
        for item in ast.walk(node)
    )


def _uses_turn_number(node: ast.AST, tainted_names: set[str]) -> bool:
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and item.id in tainted_names:
            return True
        if isinstance(item, ast.Subscript) and _string_key(item.slice) == "turn":
            return True
        if (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "get"
            and bool(item.args)
            and _string_key(item.args[0]) == "turn"
        ):
            return True
        if (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id == "len"
            and bool(item.args)
            and _contains_turn_collection(item.args[0])
        ):
            return True
    return False


def _is_integer_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)


def _value_is_turn_number(node: ast.AST, tainted_names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in tainted_names
    if isinstance(node, ast.Subscript):
        return _string_key(node.slice) == "turn"
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and bool(node.args)
        and _string_key(node.args[0]) == "turn"
    ):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len":
        return bool(node.args) and _contains_turn_collection(node.args[0])
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"int", "float", "max", "min"}:
        return any(_value_is_turn_number(argument, tainted_names) for argument in node.args)
    if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.IfExp, ast.Tuple, ast.List)):
        return any(_value_is_turn_number(child, tainted_names) for child in ast.iter_child_nodes(node))
    return False


def _scope_turn_names(scope: ast.AST, nodes: list[ast.AST]) -> set[str]:
    tainted: set[str] = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        arguments = [*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs]
        if scope.args.vararg:
            arguments.append(scope.args.vararg)
        if scope.args.kwarg:
            arguments.append(scope.args.kwarg)
        tainted.update(argument.arg for argument in arguments if _turn_named_parameter(argument))

    changed = True
    while changed:
        changed = False
        for node in nodes:
            assignments: list[tuple[ast.AST, ast.AST]] = []
            if isinstance(node, ast.Assign):
                assignments.extend((target, node.value) for target in node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                assignments.append((node.target, node.value))
            elif isinstance(node, ast.NamedExpr):
                assignments.append((node.target, node.value))
            for target, value in assignments:
                names = _target_names(target)
                if names - tainted and _value_is_turn_number(value, tainted):
                    tainted.update(names)
                    changed = True
    return tainted


def turn_number_literal_operations(app_root: Path = APP_ROOT) -> list[str]:
    """Find local numeric policy derived from a turn number or turn-history length.

    The analysis is deliberately intramodule and scope-local: it follows direct
    assignments and unpacking, and seeds parameters whose name or annotation
    identifies a turn number. Canonical ``+ 1`` progression and generic
    non-negative/positive schema bounds are not policy thresholds. The guard
    does not resolve numeric names such as module-level constants, descend into
    container literals used as thresholds (for example ``turn in (10, 11)``),
    interpret numeric-keyed table dispatch such as ``rules.get(turn)``, or
    infer values returned by another function or module.
    """
    violations: list[str] = []
    for path in sorted(app_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        scopes: list[ast.AST] = [tree, *(node for node in ast.walk(tree) if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ))]
        for scope in scopes:
            nodes = _scope_nodes(scope)
            tainted_names = _scope_turn_names(scope, nodes)
            for node in nodes:
                if isinstance(node, ast.Compare):
                    operands = [node.left, *node.comparators]
                    has_literal_operand = any(_is_integer_literal(operand) for operand in operands)
                    positive_schema_bound = (
                        len(node.ops) == 1
                        and isinstance(node.ops[0], (ast.Lt, ast.LtE))
                        and len(node.comparators) == 1
                        and _is_integer_literal(node.comparators[0])
                        and node.comparators[0].value in {0, 1}
                    )
                    is_violation = (
                        has_literal_operand
                        and not positive_schema_bound
                        and _uses_turn_number(node, tainted_names)
                    )
                elif isinstance(node, ast.BinOp):
                    literal_operands = [operand for operand in (node.left, node.right) if _is_integer_literal(operand)]
                    canonical_increment = isinstance(node.op, ast.Add) and any(operand.value == 1 for operand in literal_operands)
                    is_violation = bool(literal_operands) and not canonical_increment and _uses_turn_number(
                        node, tainted_names
                    )
                else:
                    is_violation = False
                if not is_violation:
                    continue
                segment = ast.get_source_segment(source, node) or ""
                violations.append(f"{path.relative_to(app_root)}:{node.lineno}: {segment}")
    return sorted(set(violations))


def test_gateway_has_no_numeric_literal_predicate_over_meta_turn():
    assert turn_number_literal_operations() == [], (
        "Gateway policy must not derive behavior from a numeric turn threshold; "
        f"found: {turn_number_literal_operations()}"
    )


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("direct.py", "def f(state):\n    return int(state.get('meta', {}).get('turn', 0) or 0) > 10\n"),
        ("assigned.py", "def f(state):\n    turn = state['meta']['turn']\n    return turn > 10\n"),
        ("meta_alias.py", "def f(state):\n    meta = state.get('meta', {})\n    return meta.get('turn', 0) > 10\n"),
        ("unpacked.py", "def f(state):\n    turn, label = state['meta']['turn'], 'x'\n    return turn > 10\n"),
        ("remaining.py", "def f(state):\n    turn = state['meta']['turn']\n    return max(10 - turn, 0)\n"),
        ("parameter.py", "def f(turn):\n    return turn > 10\n"),
        ("history_length.py", "def f(state):\n    return len(state.get('turns', [])) > 10\n"),
    ],
    ids=["direct", "assigned", "meta-alias", "unpacked", "remaining-binop", "turn-parameter", "history-length"],
)
def test_turn_number_literal_guard_classifies_realistic_injections(
    tmp_path: Path,
    filename: str,
    source: str,
):
    injected_app = tmp_path / "app"
    injected_app.mkdir()
    (injected_app / filename).write_text(source, encoding="utf-8")

    violations = turn_number_literal_operations(injected_app)

    assert len(violations) == 1
    assert violations[0].startswith(f"{filename}:")


def test_rollback_keeps_turns_append_only_but_excludes_overwritten_turns_from_memory(tmp_path: Path):
    sqlite_path = tmp_path / "state.db"
    state_path = tmp_path / "state.json"
    store = StateStore(str(sqlite_path), "rollback-memory", str(state_path))

    for version in (1, 2, 3):
        if version > 1:
            state = store.get_state()
            state["meta"]["state_version"] = version
            state["meta"]["turn"] = version - 1
            store.insert_state_version(state, f"test:v{version}")
        store.record_turn(
            f"turn-{version}",
            f"request-{version}",
            f"player-{version}",
            f"narrator-{version}",
            {},
            version,
        )

    assert [turn["state_version"] for turn in store.turns_for_memory()] == [1, 2, 3]

    restored = store.rollback(target_version=1)

    assert restored["meta"]["state_version"] == 4
    assert store.current_version() == 4
    assert [turn["state_version"] for turn in store.turns_for_memory()] == [1]
    assert [turn["state_version"] for turn in store.turn_history()] == [1, 2, 3]
    with sqlite3.connect(sqlite_path) as connection:
        flags = connection.execute(
            "SELECT state_version, excluded_from_memory FROM turns "
            "WHERE campaign_id = ? ORDER BY state_version",
            (store.campaign_id,),
        ).fetchall()
    assert flags == [(1, 0), (2, 1), (3, 1)]

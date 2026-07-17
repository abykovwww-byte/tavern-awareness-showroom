"""Small JSON Patch subset used by the gateway."""

from __future__ import annotations

import copy
from typing import Any


class PatchError(Exception):
    """Patch validation or application failed."""


def pointer_parts(path: str) -> list[str]:
    if not path.startswith("/"):
        raise PatchError(f"patch path must start with '/': {path}")
    if path == "/":
        raise PatchError("root-level patch is not allowed")
    return [part.replace("~1", "/").replace("~0", "~") for part in path.strip("/").split("/")]


def resolve_parent(document: Any, path: str) -> tuple[Any, str]:
    parts = pointer_parts(path)
    current = document
    for part in parts[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise PatchError(f"patch path parent does not exist: {path}") from exc
        elif isinstance(current, dict):
            if part not in current:
                raise PatchError(f"patch path parent does not exist: {path}")
            current = current[part]
        else:
            raise PatchError(f"patch path parent is not a container: {path}")
    return current, parts[-1]


def target_exists(document: Any, path: str) -> bool:
    parent, key = resolve_parent(document, path)
    if isinstance(parent, list):
        return key != "-" and key.isdigit() and int(key) < len(parent)
    if isinstance(parent, dict):
        return key in parent
    return False


def apply_operation(document: Any, operation: dict[str, Any]) -> None:
    op = operation["op"]
    path = operation["path"]
    parent, key = resolve_parent(document, path)

    if op == "add":
        if "value" not in operation:
            raise PatchError(f"add operation requires value: {path}")
        if isinstance(parent, list):
            if key == "-":
                parent.append(operation["value"])
            else:
                parent.insert(int(key), operation["value"])
        elif isinstance(parent, dict):
            parent[key] = operation["value"]
        else:
            raise PatchError(f"add target parent is not a container: {path}")
    elif op == "replace":
        if "value" not in operation:
            raise PatchError(f"replace operation requires value: {path}")
        if not target_exists(document, path):
            raise PatchError(f"replace target does not exist: {path}")
        if isinstance(parent, list):
            parent[int(key)] = operation["value"]
        else:
            parent[key] = operation["value"]
    elif op == "remove":
        if not target_exists(document, path):
            raise PatchError(f"remove target does not exist: {path}")
        if isinstance(parent, list):
            del parent[int(key)]
        else:
            del parent[key]
    else:
        raise PatchError(f"unsupported op: {op}")


def apply_patch(document: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    candidate = copy.deepcopy(document)
    for operation in operations:
        apply_operation(candidate, operation)
    return candidate

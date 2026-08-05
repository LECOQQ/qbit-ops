"""Statically forbid a raw-field or tracker-status-shape helper from being
redefined outside `qbit_core.qbit`, mirroring `tests/test_layering.py`'s
AST approach.
"""

from __future__ import annotations

import ast
from pathlib import Path

import qbit_core
import qbit_ops

PACKAGE_DIR = Path(qbit_ops.__file__).parent
CORE_DIR = Path(qbit_core.__file__).parent
BOUNDARY_DIR = CORE_DIR / "qbit"

# `qbit_ops.app_services.create_qbit_client` is a deliberate, documented
# second definition: the zero-argument CLI/TUI wrapper that loads
# `.env`/environment configuration before delegating to the boundary's
# `create_qbit_client(config)` -- see
# `tests/test_qbit_architecture.py`'s
# `test_client_construction_exists_in_exactly_two_documented_locations`.
_ALLOWED_SECOND_DEFINITION = PACKAGE_DIR / "app_services.py"

# Names owned exclusively by the qbit boundary (`qbit_core/qbit/fields.py`
# and `qbit_core/qbit/client.py`). Matched with or without a leading
# underscore to also catch a private re-implementation.
_BOUNDARY_OWNED_NAMES = frozenset(
    {
        "get_field",
        "get_field_as_string",
        "get_field_as_int",
        "get_field_as_float",
        "get_raw_tracker_status",
        "is_disabled_tracker",
        "is_disabled_tracker_status",
        "get_active_tracker_urls",
        "get_transfer_rates",
        "create_qbit_client",
    }
)


def _production_python_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _defined_function_names(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_boundary_directory_exists_and_is_discovered() -> None:
    """Guard the scan itself: the boundary package must actually be found."""
    assert BOUNDARY_DIR.is_dir()
    boundary_files = _production_python_files(BOUNDARY_DIR)
    assert boundary_files
    assert any(path.name == "fields.py" for path in boundary_files)
    assert any(path.name == "client.py" for path in boundary_files)


def test_no_boundary_owned_helper_is_redefined_outside_qbit_package() -> None:
    """A new copy of a boundary-owned helper must fail here."""
    files = [
        path
        for path in (
            _production_python_files(PACKAGE_DIR)
            + _production_python_files(CORE_DIR)
        )
        if BOUNDARY_DIR not in path.parents
        and path != _ALLOWED_SECOND_DEFINITION
    ]
    assert (
        files
    ), "expected at least one production module outside qbit_core/qbit/"
    assert any(path.name == "trackers.py" for path in files), (
        "expected qbit_core/features/trackers.py to be part of the scanned "
        "set -- an empty or wrong scan would make this test vacuously pass"
    )

    offenders: dict[str, set[str]] = {}
    for path in files:
        defined = _defined_function_names(path.read_text(encoding="utf-8"))
        leaked = {
            name
            for name in defined
            if name in _BOUNDARY_OWNED_NAMES
            or name.lstrip("_") in _BOUNDARY_OWNED_NAMES
        }
        if leaked:
            offenders[str(path)] = leaked

    assert not offenders, (
        f"boundary-owned helper(s) redefined outside qbit_core/qbit/: "
        f"{offenders} -- reuse qbit_core.qbit.fields/client instead of "
        "reimplementing"
    )

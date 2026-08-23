"""Verify `qbit_core`'s declared public surface stays truthful.

`__all__` in a `qbit_core` submodule is the explicit contract: what
`qbit_ops` and the external, Waitarr-representative consumer (see
`tests/test_qbit_core_isolated_wheel.py`) are allowed to depend on.
Before this, "public" meant only "no leading underscore" -- an ambient
default nobody had to declare, so nothing could ever drift from it.

Two things are checked, both derived from real source (never a
hand-copied name list, so this cannot silently go stale the way a
one-off audit count does):

- every name a declared `__all__` lists actually exists on the module
  (catches a typo or a stale entry after a rename);
- every name `qbit_ops` or the Waitarr wheel test actually imports from
  a module that declares `__all__` is covered by it (catches a new,
  undeclared dependency creeping into the surface).

`qbit_core.features.trackers` is excluded: it belongs to a parallel
work-item this round (tracker domain, see `.agents/DECISIONS.md`) and
is deliberately left out of scope here.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from collections import defaultdict
from pathlib import Path

import qbit_core
import qbit_ops

CORE_PACKAGE_DIR = Path(qbit_core.__file__).parent
APP_PACKAGE_DIR = Path(qbit_ops.__file__).parent
WHEEL_TEST_FILE = Path(__file__).parent / "test_qbit_core_isolated_wheel.py"

# Deliberately excluded -- see module docstring.
_OUT_OF_SCOPE_MODULES = {"qbit_core.features.trackers"}


def _qbit_core_module_names() -> list[str]:
    names = [qbit_core.__name__]
    for module_info in pkgutil.walk_packages(
        qbit_core.__path__, prefix=f"{qbit_core.__name__}."
    ):
        names.append(module_info.name)
    return sorted(n for n in names if n not in _OUT_OF_SCOPE_MODULES)


def _imports_from_qbit_core(source: str) -> dict[str, set[str]]:
    """Map each `qbit_core*` module imported-from to the names taken."""
    found: dict[str, set[str]] = defaultdict(set)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (
                node.module == "qbit_core"
                or node.module.startswith("qbit_core.")
            )
        ):
            found[node.module].update(alias.name for alias in node.names)
    return found


def _qbit_ops_consumption() -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    for path in APP_PACKAGE_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for module, names in _imports_from_qbit_core(
            path.read_text(encoding="utf-8")
        ).items():
            found[module] |= names
    return found


def _waitarr_wheel_consumption() -> dict[str, set[str]]:
    """The embedded smoke script `test_qbit_core_isolated_wheel.py` runs
    against a `qbit_core`-only wheel -- the one external, Waitarr-facing
    contract this repo already exercises for real."""
    tree = ast.parse(WHEEL_TEST_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "_CONSUMER_SMOKE_SCRIPT"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    return _imports_from_qbit_core(node.value.value)
    raise AssertionError(
        f"{WHEEL_TEST_FILE} no longer defines _CONSUMER_SMOKE_SCRIPT as a "
        "plain string constant -- update this gate's extraction."
    )


def test_every_declared_all_entry_exists_on_its_module() -> None:
    for module_name in _qbit_core_module_names():
        module = importlib.import_module(module_name)
        declared = getattr(module, "__all__", None)
        if declared is None:
            continue
        missing = [name for name in declared if not hasattr(module, name)]
        assert not missing, f"{module_name}.__all__ names missing: {missing}"


def test_qbit_ops_and_waitarr_only_use_the_declared_surface() -> None:
    consumption: dict[str, set[str]] = defaultdict(set)
    for module, names in _qbit_ops_consumption().items():
        consumption[module] |= names
    for module, names in _waitarr_wheel_consumption().items():
        consumption[module] |= names

    violations: dict[str, set[str]] = {}
    for module_name, used_names in consumption.items():
        if module_name in _OUT_OF_SCOPE_MODULES:
            continue
        module = importlib.import_module(module_name)
        declared = getattr(module, "__all__", None)
        if declared is None:
            # No declared surface yet for this module -- not this
            # gate's job to force one into existence; see the
            # completeness check below for that.
            continue
        undeclared = used_names - set(declared)
        if undeclared:
            violations[module_name] = undeclared

    assert not violations, (
        "qbit_ops or the Waitarr wheel test import names not covered by "
        f"the module's declared __all__: {violations}"
    )


def test_every_consumed_module_declares_a_surface() -> None:
    """A module `qbit_ops` or the Waitarr wheel test actually imports
    from must declare `__all__` -- otherwise a real dependency exists
    with no explicit contract behind it, exactly the gap this file
    closes."""
    consumption: dict[str, set[str]] = defaultdict(set)
    for module, names in _qbit_ops_consumption().items():
        consumption[module] |= names
    for module, names in _waitarr_wheel_consumption().items():
        consumption[module] |= names

    undeclared_modules = []
    for module_name in consumption:
        if module_name in _OUT_OF_SCOPE_MODULES:
            continue
        module = importlib.import_module(module_name)
        if getattr(module, "__all__", None) is None:
            undeclared_modules.append(module_name)

    assert not undeclared_modules, (
        "modules with a real cross-package consumer but no declared "
        f"__all__: {sorted(undeclared_modules)}"
    )

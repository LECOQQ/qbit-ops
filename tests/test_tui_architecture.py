"""Static + smoke guarantees for the TUI reorg (see docs/DECISIONS.md).

Mirrors `tests/test_cli_architecture.py`'s approach for the CLI split:
AST-based structural checks (a moved class is defined in exactly one
place, `app.py` no longer duplicates it, worker ownership stays
unambiguous) plus a handful of real `run_test()` mounts proving the
extracted widgets/modals actually compose in isolation -- not just
"imports without raising".

Every check here is deliberately non-vacuous: each also has a
self-check (asserting the scan itself finds a non-empty, expected
baseline) so a scan that silently finds nothing cannot pass this file
by accident -- the same discipline `test_tui_security.py`/
`test_layering.py` already use.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.screen import ModalScreen

import qbit_ops
import qbit_ops.tui
import qbit_ops.tui.app
from qbit_ops.tui.modals.actions import ActionsScreen
from qbit_ops.tui.modals.help import HelpScreen
from qbit_ops.tui.widgets.details import DetailsPanel
from qbit_ops.tui.widgets.filters import FiltersPanel
from qbit_ops.tui.widgets.overview import OverviewPanel, WorkspaceTabs
from qbit_ops.tui.widgets.status_bar import (
    ConnectionBanner,
    FilterSummary,
    LastActionBar,
)
from tests.support import make_torrent

pytestmark = pytest.mark.tui

APP_PACKAGE_DIR = Path(qbit_ops.__file__).parent
TUI_PACKAGE_DIR = Path(qbit_ops.tui.__file__).parent

# Every class the TUI reorg extracted out of `qbit_ops/tui/app.py`, and
# the one module each now canonically lives in -- deliberately a fixed
# baseline (not derived from a scan) so a future accidental duplicate
# definition has something concrete to be caught against.
_CANONICAL_HOME: dict[str, str] = {
    "QbitOpsTuiApp": "app.py",
    "MainScreen": "app.py",
    "WorkspaceTabs": "widgets/overview.py",
    "OverviewPanel": "widgets/overview.py",
    "FiltersPanel": "widgets/filters.py",
    "DetailsPanel": "widgets/details.py",
    "ConnectionBanner": "widgets/status_bar.py",
    "LastActionBar": "widgets/status_bar.py",
    "FilterSummary": "widgets/status_bar.py",
    "HelpScreen": "modals/help.py",
    "FiltersScreen": "modals/filters.py",
    "DetailsScreen": "modals/details.py",
    "ExplainScreen": "modals/explain.py",
    "ActionsScreen": "modals/actions.py",
    "PreviewScreen": "modals/preview.py",
    "ResultScreen": "modals/result.py",
    "MutationUiResult": "state.py",
}


def _class_definitions_by_file() -> dict[str, list[Path]]:
    """Map each top-level class name defined anywhere under `tui/` to
    every file that defines it (via `ast`, so a docstring/comment
    mentioning the name is never mistaken for a definition)."""
    by_name: dict[str, list[Path]] = {}
    for path in sorted(TUI_PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                by_name.setdefault(node.name, []).append(path)
    return by_name


def test_class_definition_scan_is_non_vacuous() -> None:
    definitions = _class_definitions_by_file()
    assert definitions, "expected at least one class definition under tui/"
    assert "QbitOpsTuiApp" in definitions


def test_every_extracted_class_is_defined_in_exactly_one_canonical_module() -> (
    None
):
    """No moved class was left duplicated in `app.py` after extraction,
    and each lives exactly where the reorg plan put it."""
    definitions = _class_definitions_by_file()
    for name, relative_home in _CANONICAL_HOME.items():
        files = definitions.get(name, [])
        assert len(files) == 1, (
            f"expected exactly one definition of {name} under tui/, "
            f"found {[str(f) for f in files]}"
        )
        expected = TUI_PACKAGE_DIR / relative_home
        assert (
            files[0] == expected
        ), f"{name} is defined in {files[0]}, expected {expected}"


def test_root_app_class_exists_exactly_once() -> None:
    definitions = _class_definitions_by_file()
    assert definitions.get("QbitOpsTuiApp") == [TUI_PACKAGE_DIR / "app.py"]


def _run_worker_call_sites() -> list[Path]:
    """Files under `tui/` containing a `self.run_worker(...)` call."""
    sites = []
    for path in sorted(TUI_PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run_worker"
            ):
                sites.append(path)
                break
    return sites


def test_only_app_py_dispatches_workers() -> None:
    """Refresh/detail/mutation worker ownership stays unambiguous: no
    extracted widget or modal was given its own `run_worker` call --
    see `qbit_ops.tui.app`'s worker-hardening docstring. A smaller
    `app.py` is not worth weakening this ownership guarantee.
    """
    sites = _run_worker_call_sites()
    assert sites, "expected at least one run_worker call site under tui/"
    assert sites == [TUI_PACKAGE_DIR / "app.py"]


def _bound_action_names(source: str) -> set[str]:
    """Extract every `Binding("key", "action", ...)` action string from
    a module-level `BINDINGS = [...]` assignment."""
    tree = ast.parse(source)
    actions: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "BINDINGS"
                for target in node.targets
            )
            and isinstance(node.value, ast.List)
        ):
            for element in node.value.elts:
                if (
                    isinstance(element, ast.Call)
                    and len(element.args) >= 2
                    and isinstance(element.args[1], ast.Constant)
                    and isinstance(element.args[1].value, str)
                ):
                    actions.add(element.args[1].value)
    return actions


# Baseline of `QbitOpsTuiApp.BINDINGS` action names, captured before the
# TUI reorg moved widgets/modals out of `app.py` -- this class's own
# BINDINGS list was never touched by the move (see docs/DECISIONS.md),
# so every one of these must still be present verbatim.
_EXPECTED_APP_BINDING_ACTIONS = {
    "quit",
    "show_overview",
    "show_torrents",
    "cursor_down",
    "cursor_up",
    "focus_search",
    "open_filters",
    "activate",
    "copy_hash",
    "explain",
    "refresh_details",
    "toggle_selection",
    "select_all_visible",
    "deselect_all",
    "open_actions",
    "toggle_help",
}


def test_app_level_bindings_are_all_still_registered() -> None:
    source = (TUI_PACKAGE_DIR / "app.py").read_text(encoding="utf-8")
    actions = _bound_action_names(source)
    missing = _EXPECTED_APP_BINDING_ACTIONS - actions
    assert not missing, f"App-level bindings lost during the reorg: {missing}"


@pytest.mark.parametrize(
    ("relative_path", "expected_actions"),
    [
        ("modals/help.py", {"dismiss"}),
        ("modals/filters.py", {"clear"}),
        ("modals/actions.py", {"dismiss"}),
        ("modals/preview.py", set()),
        ("modals/result.py", set()),
    ],
)
def test_modal_bindings_are_still_registered(
    relative_path: str, expected_actions: set[str]
) -> None:
    source = (TUI_PACKAGE_DIR / relative_path).read_text(encoding="utf-8")
    actions = _bound_action_names(source)
    missing = expected_actions - actions
    assert not missing, f"{relative_path} lost bindings: {missing}"


def test_no_application_module_imports_qbit_ops_tui() -> None:
    """Only `qbit_ops.cli.commands.tui` (deferred, inside the command
    body) and `qbit_ops.tui` itself may ever reference `qbit_ops.tui` --
    every other production module must stay ignorant of the TUI layer.
    """
    checked = 0
    for path in sorted(APP_PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if TUI_PACKAGE_DIR in path.parents or path == TUI_PACKAGE_DIR:
            continue
        if path == APP_PACKAGE_DIR / "cli" / "commands" / "tui.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not (
                        alias.name == "qbit_ops.tui"
                        or alias.name.startswith("qbit_ops.tui.")
                    ), f"{path} imports {alias.name} at module level"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not (
                    node.module == "qbit_ops.tui"
                    or node.module.startswith("qbit_ops.tui.")
                ), f"{path} imports from {node.module} at module level"
        checked += 1
    assert checked > 5, "expected to scan more than a handful of files"


class _Harness(App[None]):
    """A minimal App used only to mount one widget/modal in isolation."""

    def __init__(self, child: object) -> None:
        super().__init__()
        self._child = child

    def compose(self) -> ComposeResult:
        yield self._child  # type: ignore[misc]


@pytest.mark.parametrize(
    "widget_factory",
    [WorkspaceTabs, OverviewPanel, FiltersPanel, DetailsPanel],
)
async def test_extracted_content_widgets_mount_in_isolation(
    widget_factory: type,
) -> None:
    app = _Harness(widget_factory())
    async with app.run_test():
        assert app.query(widget_factory)


@pytest.mark.parametrize(
    "widget_factory", [ConnectionBanner, LastActionBar, FilterSummary]
)
async def test_extracted_marker_widgets_mount_in_isolation(
    widget_factory: type,
) -> None:
    app = _Harness(widget_factory())
    async with app.run_test():
        assert app.query(widget_factory)


async def test_overview_panel_renders_real_state_in_isolation() -> None:
    """Not just "mounts" -- `render_state` (the method `QbitOpsTuiApp`
    actually calls) must still work now that `OverviewPanel` lives in
    its own module and imports its formatting helpers from
    `qbit_ops.tui.formatting` instead of sharing `app.py`'s module
    namespace."""
    from qbit_ops.tui.state import TuiState

    app = _Harness(OverviewPanel())
    async with app.run_test():
        panel = app.query_one(OverviewPanel)
        panel.render_state(TuiState())


class _ModalHarness(App[None]):
    """A minimal App used to push one modal in isolation."""

    def __init__(self, modal: ModalScreen[None]) -> None:
        super().__init__()
        self._modal = modal

    def on_mount(self) -> None:
        self.push_screen(self._modal)


async def test_help_modal_composes_successfully_in_isolation() -> None:
    app = _ModalHarness(HelpScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)


async def test_actions_modal_composes_successfully_in_isolation() -> None:
    torrent = make_torrent()
    app = _ModalHarness(ActionsScreen((torrent["hash"],), (torrent["name"],)))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ActionsScreen)

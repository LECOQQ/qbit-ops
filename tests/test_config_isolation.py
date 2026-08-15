"""Prove the suite cannot resolve the operator's real qBittorrent.

These tests exercise the **actual resolution** -- `load_qbit_config()`
and `_get_default_env_files()` -- rather than asserting that the
isolation fixture set some variables. Checking the premises would pass
even if `qbit_ops.config` stopped reading them.

Scope, stated precisely because a guard that suggests more than it
proves is worse than none:

- **proven**: under `pytest`, configuration resolution lands on the
  fake instance, and neither the repository's own `.env` nor a
  user-level config can be loaded;
- **not proven**: that an arbitrary Python or shell command run outside
  `pytest` is isolated. Nothing here constrains that path -- see
  `.agents/WORKFLOW.md`.
"""

from __future__ import annotations

from pathlib import Path

from qbit_ops import config as config_module
from tests.conftest import ISOLATED_QBIT_HOST


def test_resolved_configuration_is_the_fake_instance() -> None:
    """The end-to-end effect, not the seams that produce it."""
    resolved = config_module.load_qbit_config()

    assert resolved.host == ISOLATED_QBIT_HOST
    assert resolved.username == "isolated-user"


def test_a_real_env_file_may_sit_next_to_the_tests_and_still_lose() -> None:
    """The risk is not hypothetical, and the guard does not remove it.

    `_get_default_env_files()` still names `cwd()/.env`, which on a
    developer machine exists and holds live credentials. Isolation does
    not delete that file or stop it being *named* -- it stops it being
    *loaded*, because `QBIT_OPS_ENV_FILE` makes `_load_env_files()`
    return before the default list is ever consulted.

    Asserting that the path does not exist would therefore pass for the
    wrong reason on a machine that simply has no `.env`.
    """
    named = config_module._get_default_env_files()
    assert any(path.name == ".env" for path in named)

    config_module._load_env_files()

    assert config_module.load_qbit_config().host == ISOLATED_QBIT_HOST


def test_the_repository_env_file_is_never_loaded() -> None:
    """The strongest statement available: even where a real `.env`
    sits next to the tests, its values never win.

    `load_dotenv(override=False)` cannot overwrite an already-set
    variable, and the fixture sets all three before anything loads.
    """
    resolved = config_module.load_qbit_config()

    assert resolved.host == ISOLATED_QBIT_HOST
    assert "localhost" not in resolved.host
    assert resolved.password == "isolated-password"


def test_the_env_file_override_points_at_a_path_that_cannot_exist() -> None:
    """`QBIT_OPS_ENV_FILE` short-circuits default resolution entirely.

    This is the seam that makes the other two locations unreachable
    rather than merely redirected, so it is worth pinning that it is
    set and that it resolves to nothing.
    """
    import os

    override = os.getenv(config_module.APP_ENV_FILE_VARIABLE)

    assert override is not None
    assert not Path(override).exists()

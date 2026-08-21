"""Contract of the guided-setup domain shared by `init` and the TUI.

Deliberately exercises the real write/read round trip through
`qbit_ops.config.load_qbit_config` rather than asserting on the file's
text: what matters is that a value written comes back unchanged, not
how it was quoted.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from qbit_core.config import CONNECTION_VARIABLES, QbitConfig
from qbit_core.errors import (
    InvalidInputError,
    QbitAuthenticationError,
    QbitConnectionError,
)
from qbit_core.features.connection_setup import (
    ENV_DIRECTORY_MODE,
    ENV_FILE_MODE,
    EnvFileExistsError,
    MaskingKind,
    build_connection_config,
    detect_masking_sources,
    render_env_file,
    try_connection,
    write_connection_env_file,
)
from qbit_ops.config import (
    APP_ENV_FILE_VARIABLE,
    PROJECT_ENV_FILE,
    collect_masking_sources,
    get_user_env_file,
    load_qbit_config,
)

EXPLICIT_ENV_FILE = "/somewhere/else/.env"


def _config(**overrides: str) -> QbitConfig:
    values = {
        "host": "http://localhost:8080",
        "username": "admin",
        "password": "secret",
    }
    values.update(overrides)
    return QbitConfig(**values)  # type: ignore[arg-type]


def _file_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- input validation ----------------------------------------------------


def test_build_connection_config_strips_every_value() -> None:
    config = build_connection_config(
        host="  http://h:1  ", username=" admin ", password=" pw "
    )

    assert config == QbitConfig(
        host="http://h:1", username="admin", password="pw"
    )


@pytest.mark.parametrize("field", ["host", "username", "password"])
def test_build_connection_config_rejects_a_blank_value(field: str) -> None:
    values = {"host": "http://h:1", "username": "admin", "password": "pw"}
    values[field] = "   "

    with pytest.raises(InvalidInputError):
        build_connection_config(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["a${HOME}b", "a\nb", "a\rb"])
def test_build_connection_config_rejects_an_unrepresentable_value(
    value: str,
) -> None:
    """A value the env-file loader would not read back unchanged."""
    with pytest.raises(InvalidInputError):
        build_connection_config(
            host="http://h:1", username="admin", password=value
        )


# --- rendering and the write/read round trip -----------------------------


def test_render_env_file_writes_exactly_the_three_variables() -> None:
    body = render_env_file(_config())

    assigned = [line.split("=", 1)[0] for line in body.splitlines()]
    assert assigned == list(CONNECTION_VARIABLES)


@pytest.mark.parametrize(
    "password",
    [
        "simple",
        "with space",
        "hash#comment",
        "trailing #comment",
        'quote"inside',
        "back\\slash",
        "trailing\\",
        "dollar$sign",
        "=equals=",
        "'single'",
    ],
)
def test_a_written_value_is_read_back_unchanged(
    password: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv(APP_ENV_FILE_VARIABLE, raising=False)
    for variable in CONNECTION_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    working_directory = tmp_path / "cwd"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)

    config = build_connection_config(
        host="http://localhost:8080", username="ad min", password=password
    )
    write_connection_env_file(get_user_env_file(), config)

    assert load_qbit_config() == config


# --- file permissions ----------------------------------------------------


def test_the_written_file_is_created_with_owner_only_permissions(
    tmp_path: Path,
) -> None:
    target = tmp_path / "nested" / ".env"

    written = write_connection_env_file(target, _config())

    assert _file_mode(written) == ENV_FILE_MODE


def test_the_parent_directory_is_created_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "nested" / ".env"

    write_connection_env_file(target, _config())

    assert _file_mode(target.parent) == ENV_DIRECTORY_MODE


def test_overwriting_a_world_readable_file_restores_owner_only_permissions(
    tmp_path: Path,
) -> None:
    """`O_CREAT`'s mode only applies to a file being created: an
    overwrite would otherwise leave the old permissions in place."""
    target = tmp_path / ".env"
    target.write_text("QBIT_HOST=old\n", encoding="utf-8")
    os.chmod(target, 0o644)

    write_connection_env_file(target, _config(), force=True)

    assert _file_mode(target) == ENV_FILE_MODE


# --- overwrite protection ------------------------------------------------


def test_writing_over_an_existing_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("QBIT_HOST=old\n", encoding="utf-8")

    with pytest.raises(EnvFileExistsError) as raised:
        write_connection_env_file(target, _config())

    assert raised.value.path == target
    assert target.read_text(encoding="utf-8") == "QBIT_HOST=old\n"


def test_force_overwrites_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("QBIT_HOST=old\n", encoding="utf-8")

    write_connection_env_file(target, _config(host="http://new:1"), force=True)

    assert "http://new:1" in target.read_text(encoding="utf-8")
    assert "old" not in target.read_text(encoding="utf-8")


# --- connection test -----------------------------------------------------


def test_a_successful_login_reports_a_successful_attempt() -> None:
    attempt = try_connection(_config(), client_factory=lambda config: object())

    assert attempt.ok is True
    assert attempt.detail is None


@pytest.mark.parametrize(
    "error",
    [
        QbitConnectionError("Unable to connect."),
        QbitAuthenticationError("Authentication failed."),
        OSError("Network is unreachable."),
    ],
)
def test_a_recognized_failure_reports_a_failed_attempt(
    error: Exception,
) -> None:
    def _raise(config: QbitConfig) -> object:
        raise error

    attempt = try_connection(_config(), client_factory=_raise)

    assert attempt.ok is False
    assert attempt.detail == str(error)


def test_an_unexpected_failure_is_never_reported_as_a_connection_problem() -> (
    None
):
    def _raise(config: QbitConfig) -> object:
        raise ValueError("programming defect")

    with pytest.raises(ValueError):
        try_connection(_config(), client_factory=_raise)


# --- masking classification ----------------------------------------------


def test_no_masking_source_is_reported_when_nothing_outranks_the_file() -> None:
    sources = detect_masking_sources(
        Path("/home/x/.config/qbit-ops/.env"),
        environment={},
        explicit_env_file_variable=APP_ENV_FILE_VARIABLE,
    )

    assert sources == ()


@pytest.mark.parametrize("variable", CONNECTION_VARIABLES)
def test_an_exported_variable_is_reported_and_named(variable: str) -> None:
    target = Path("/home/x/.config/qbit-ops/.env")

    sources = detect_masking_sources(
        target,
        environment={variable: "value"},
        explicit_env_file_variable=APP_ENV_FILE_VARIABLE,
    )

    assert [source.kind for source in sources] == [MaskingKind.ENVIRONMENT]
    assert sources[0].variable == variable
    assert variable in sources[0].message


def test_a_blank_exported_variable_does_not_mask_anything() -> None:
    """`load_qbit_config` treats a blank value as missing, so it wins
    over nothing."""
    sources = detect_masking_sources(
        Path("/home/x/.config/qbit-ops/.env"),
        environment={"QBIT_HOST": "   "},
        explicit_env_file_variable=APP_ENV_FILE_VARIABLE,
    )

    assert sources == ()


def test_a_project_env_file_is_reported_and_named() -> None:
    project = Path("/work/.env")

    sources = detect_masking_sources(
        Path("/home/x/.config/qbit-ops/.env"),
        environment={},
        explicit_env_file_variable=APP_ENV_FILE_VARIABLE,
        project_env_file=project,
        project_env_variables=("QBIT_HOST",),
    )

    assert [source.kind for source in sources] == [MaskingKind.PROJECT_ENV_FILE]
    assert str(project) in sources[0].message
    assert "QBIT_HOST" in sources[0].message


def test_an_env_file_override_pointing_elsewhere_is_reported() -> None:
    sources = detect_masking_sources(
        Path("/home/x/.config/qbit-ops/.env"),
        environment={APP_ENV_FILE_VARIABLE: EXPLICIT_ENV_FILE},
        explicit_env_file_variable=APP_ENV_FILE_VARIABLE,
    )

    assert [source.kind for source in sources] == [
        MaskingKind.EXPLICIT_ENV_FILE
    ]
    assert EXPLICIT_ENV_FILE in sources[0].message
    assert APP_ENV_FILE_VARIABLE in sources[0].message


def test_an_env_file_override_pointing_at_the_target_is_not_reported() -> None:
    target = Path("/home/x/.config/qbit-ops/.env")

    sources = detect_masking_sources(
        target,
        environment={APP_ENV_FILE_VARIABLE: str(target)},
        explicit_env_file_variable=APP_ENV_FILE_VARIABLE,
    )

    assert sources == ()


def test_an_exported_variable_is_reported_even_under_an_env_file_override() -> (
    None
):
    """An exported variable beats every file, override included."""
    sources = detect_masking_sources(
        Path("/home/x/.config/qbit-ops/.env"),
        environment={
            APP_ENV_FILE_VARIABLE: EXPLICIT_ENV_FILE,
            "QBIT_HOST": "http://elsewhere:1",
        },
        explicit_env_file_variable=APP_ENV_FILE_VARIABLE,
    )

    assert [source.kind for source in sources] == [
        MaskingKind.EXPLICIT_ENV_FILE,
        MaskingKind.ENVIRONMENT,
    ]


# --- masking observation -------------------------------------------------


def _isolate_configuration_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv(APP_ENV_FILE_VARIABLE, raising=False)
    for variable in CONNECTION_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    working_directory = tmp_path / "cwd"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    return working_directory


def test_collect_reports_nothing_when_the_written_file_is_the_only_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_configuration_sources(monkeypatch, tmp_path)

    assert collect_masking_sources(get_user_env_file()) == ()


def test_collect_reports_a_project_env_file_in_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    working_directory = _isolate_configuration_sources(monkeypatch, tmp_path)
    (working_directory / PROJECT_ENV_FILE).write_text(
        "QBIT_HOST=http://elsewhere:1\n", encoding="utf-8"
    )

    sources = collect_masking_sources(get_user_env_file())

    assert [source.kind for source in sources] == [MaskingKind.PROJECT_ENV_FILE]
    assert str(working_directory / PROJECT_ENV_FILE) in sources[0].message


def test_collect_reports_an_exported_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_configuration_sources(monkeypatch, tmp_path)
    monkeypatch.setenv("QBIT_PASSWORD", "exported")

    sources = collect_masking_sources(get_user_env_file())

    assert [source.kind for source in sources] == [MaskingKind.ENVIRONMENT]
    assert sources[0].variable == "QBIT_PASSWORD"


def test_collect_ignores_a_project_env_file_under_an_env_file_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override short-circuits file resolution entirely, so the
    working directory's `.env` never gets read."""
    working_directory = _isolate_configuration_sources(monkeypatch, tmp_path)
    (working_directory / PROJECT_ENV_FILE).write_text(
        "QBIT_HOST=http://elsewhere:1\n", encoding="utf-8"
    )
    monkeypatch.setenv(APP_ENV_FILE_VARIABLE, EXPLICIT_ENV_FILE)

    sources = collect_masking_sources(get_user_env_file())

    assert [source.kind for source in sources] == [
        MaskingKind.EXPLICIT_ENV_FILE
    ]

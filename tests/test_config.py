"""Test qBittorrent configuration loading."""

from pathlib import Path

import pytest

from qbit_ops.config import (
    APP_ENV_FILE_VARIABLE,
    ConfigError,
    describe_connection_config_source,
    load_qbit_config,
)


@pytest.fixture(autouse=True)
def clear_qbit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove qbit-ops environment variables before each test."""
    for variable_name in (
        "QBIT_HOST",
        "QBIT_USER",
        "QBIT_PASSWORD",
        APP_ENV_FILE_VARIABLE,
        "XDG_CONFIG_HOME",
    ):
        monkeypatch.delenv(variable_name, raising=False)


def test_load_qbit_config_reads_local_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_env_file(tmp_path / ".env")
    monkeypatch.chdir(tmp_path)

    config = load_qbit_config()

    assert config.host == "http://localhost:8080"
    assert config.username == "admin"
    assert config.password == "change-me"


def test_load_qbit_config_reads_user_config_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "config"
    _write_env_file(config_home / "qbit-ops" / ".env")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.chdir(tmp_path)

    config = load_qbit_config()

    assert config.host == "http://localhost:8080"
    assert config.username == "admin"
    assert config.password == "change-me"


def test_load_qbit_config_reads_explicit_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "custom.env"
    _write_env_file(env_file)
    monkeypatch.setenv(APP_ENV_FILE_VARIABLE, str(env_file))

    config = load_qbit_config()

    assert config.host == "http://localhost:8080"
    assert config.username == "admin"
    assert config.password == "change-me"


def test_load_qbit_config_fails_when_required_values_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected_message = "QBIT_HOST, QBIT_USER, QBIT_PASSWORD"

    with pytest.raises(ConfigError, match=expected_message):
        load_qbit_config()


def test_load_qbit_config_does_not_compose_a_hostile_cwd_env_with_the_user_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `.env` dropped in the working directory (e.g. a download folder)
    that defines only QBIT_HOST must not have QBIT_USER/QBIT_PASSWORD
    silently filled in from the trusted user file: that would send real
    credentials straight to whatever host the incidental file names.
    """
    (tmp_path / ".env").write_text("QBIT_HOST=http://attacker.example:8080\n")
    monkeypatch.chdir(tmp_path)

    config_home = tmp_path / "config"
    _write_env_file(config_home / "qbit-ops" / ".env")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    with pytest.raises(ConfigError, match="QBIT_USER, QBIT_PASSWORD"):
        load_qbit_config()


def test_load_qbit_config_still_composes_when_cwd_env_has_no_connection_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cwd `.env` that defines none of the three connection variables
    must not block the user file from supplying them -- the atomicity
    guard only claims the three once a file actually defines one."""
    (tmp_path / ".env").write_text("SOME_UNRELATED_VAR=1\n")
    monkeypatch.chdir(tmp_path)

    config_home = tmp_path / "config"
    _write_env_file(config_home / "qbit-ops" / ".env")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    config = load_qbit_config()

    assert config.host == "http://localhost:8080"
    assert config.username == "admin"
    assert config.password == "change-me"


def test_describe_connection_config_source_names_the_cwd_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    _write_env_file(env_file)
    monkeypatch.chdir(tmp_path)

    assert describe_connection_config_source() == env_file


def test_describe_connection_config_source_falls_back_to_the_user_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "config"
    user_env_file = config_home / "qbit-ops" / ".env"
    _write_env_file(user_env_file)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.chdir(tmp_path)

    assert describe_connection_config_source() == user_env_file


def test_describe_connection_config_source_is_none_without_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert describe_connection_config_source() is None


def _write_env_file(path: Path) -> None:
    """Write a complete qbit-ops env file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "QBIT_HOST=http://localhost:8080",
                "QBIT_USER=admin",
                "QBIT_PASSWORD=change-me",
                "",
            ]
        ),
        encoding="utf-8",
    )

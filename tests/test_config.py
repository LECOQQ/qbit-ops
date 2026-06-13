"""Test qBittorrent configuration loading."""

from pathlib import Path

import pytest

from app.config import (
    APP_ENV_FILE_VARIABLE,
    ConfigError,
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
    """Ensure local `.env` files are supported for project usage."""
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
    """Ensure user-level config works for installed applications."""
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
    """Ensure explicit env files can be selected."""
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
    """Ensure missing qBittorrent settings fail explicitly."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected_message = "QBIT_HOST, QBIT_USER, QBIT_PASSWORD"

    with pytest.raises(ConfigError, match=expected_message):
        load_qbit_config()


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

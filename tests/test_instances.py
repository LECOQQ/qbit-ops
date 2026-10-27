from __future__ import annotations

import pytest

from qbit_core.config import QbitConfig
from qbit_core.errors import InvalidInputError
from qbit_core.instances import (
    QbitInstance,
    instances_from_environment,
    validate_instance_name,
)

_SECRET = "s3cret-value-never-in-messages"


def _env(**extra: str) -> dict[str, str]:
    return dict(extra)


def test_without_declaration_one_default_instance() -> None:
    env = _env(QBIT_HOST="http://h:8080", QBIT_USER="u", QBIT_PASSWORD="p")

    assert instances_from_environment(env) == (
        QbitInstance("default", QbitConfig("http://h:8080", "u", "p")),
    )


def test_blank_declaration_is_treated_as_absent() -> None:
    env = _env(
        QBIT_INSTANCES="  ", QBIT_HOST="h", QBIT_USER="u", QBIT_PASSWORD="p"
    )

    assert [i.name for i in instances_from_environment(env)] == ["default"]


def test_named_instances_keep_declaration_order() -> None:
    env = _env(
        QBIT_INSTANCES="main, share-wood",
        QBIT_MAIN_HOST="http://a",
        QBIT_MAIN_USER="ua",
        QBIT_MAIN_PASSWORD="pa",
        QBIT_SHARE_WOOD_HOST="http://b",
        QBIT_SHARE_WOOD_USER="ub",
        QBIT_SHARE_WOOD_PASSWORD="pb",
        QBIT_HOST="ignored",
    )

    assert instances_from_environment(env) == (
        QbitInstance("main", QbitConfig("http://a", "ua", "pa")),
        QbitInstance("share-wood", QbitConfig("http://b", "ub", "pb")),
    )


def test_missing_variables_are_all_named_without_values() -> None:
    env = _env(
        QBIT_INSTANCES="main", QBIT_MAIN_HOST="http://a", QBIT_MAIN_USER=" "
    )

    with pytest.raises(InvalidInputError) as caught:
        instances_from_environment(env)

    message = str(caught.value)
    assert "QBIT_MAIN_USER" in message
    assert "QBIT_MAIN_PASSWORD" in message
    assert "QBIT_MAIN_HOST" not in message


def test_password_value_never_leaks_into_errors() -> None:
    env = _env(
        QBIT_INSTANCES="main,main",
        QBIT_MAIN_HOST="h",
        QBIT_MAIN_USER="u",
        QBIT_MAIN_PASSWORD=_SECRET,
    )

    with pytest.raises(InvalidInputError) as caught:
        instances_from_environment(env)

    assert _SECRET not in str(caught.value)


def test_missing_default_variables_are_named() -> None:
    with pytest.raises(InvalidInputError, match="QBIT_HOST"):
        instances_from_environment({})


@pytest.mark.parametrize("declared", ["main,main", "a-b,a_b"])
def test_duplicate_names_are_rejected(declared: str) -> None:
    with pytest.raises(InvalidInputError, match="unique"):
        instances_from_environment(_env(QBIT_INSTANCES=declared))


@pytest.mark.parametrize("declared", ["main,", ",main", "Main", "a b"])
def test_bad_declared_names_are_rejected(declared: str) -> None:
    with pytest.raises(InvalidInputError, match="Invalid instance name"):
        instances_from_environment(_env(QBIT_INSTANCES=declared))


@pytest.mark.parametrize("name", ["a", "main", "a-b_c9", "x" * 32])
def test_valid_names_pass_through(name: str) -> None:
    assert validate_instance_name(name) == name


@pytest.mark.parametrize("name", ["", "Main", "a b", "a.b", "x" * 33, "é"])
def test_invalid_names_raise(name: str) -> None:
    with pytest.raises(InvalidInputError):
        validate_instance_name(name)

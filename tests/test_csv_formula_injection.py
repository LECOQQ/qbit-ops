from __future__ import annotations

import csv
import io

import pytest

from qbit_ops.cli.rendering import print_csv_rows


def _cells(
    capsys: pytest.CaptureFixture[str], row: tuple[str, ...]
) -> list[str]:
    print_csv_rows(["a"] * len(row), [row])
    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    return rows[1]


@pytest.mark.parametrize(
    "value",
    [
        '=HYPERLINK("http://x","y")',
        "+1+1",
        "-1+1",
        "@SUM(A1)",
        "\tcmd",
        "\rcmd",
    ],
)
def test_formula_cells_are_neutralised(
    capsys: pytest.CaptureFixture[str], value: str
) -> None:
    (cell,) = _cells(capsys, (value,))

    assert cell == "'" + value


@pytest.mark.parametrize(
    "value", ["-1", "+5", "-0.25", "1.5", "Ubuntu ISO", ""]
)
def test_plain_numbers_and_text_are_untouched(
    capsys: pytest.CaptureFixture[str], value: str
) -> None:
    (cell,) = _cells(capsys, (value,))

    assert cell == value

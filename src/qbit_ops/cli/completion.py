"""Shell-completion callbacks for options whose values are a closed set.

Only an enumeration knowable **without** talking to qBittorrent belongs
here. `--category`, `--tag` and `--tracker` are deliberately absent:
their values live on the instance, and completing them would mean an
API call on every Tab press -- a shell that hangs on a stalled seedbox,
or errors outright when it is off. See `.agents/specs/list-sort.md`
("--sort" reuses this same mechanism) for the next consumer.
"""

from collections.abc import Callable, Iterable


def complete_choices(values: Iterable[str]) -> Callable[[str], list[str]]:
    """Build a Typer `autocompletion` callback for a fixed set of values.

    `values` is consumed once, at declaration time, into a sorted
    literal list -- completion itself is a prefix filter over that
    list, so it is pure, synchronous and cannot reach the network,
    matching how Typer/Click already complete a native `Enum`/`Literal`
    option (`click.types.Choice.shell_complete`, case-sensitive
    `str.startswith`).
    """
    choices = sorted(set(values))

    def _complete(incomplete: str) -> list[str]:
        return [choice for choice in choices if choice.startswith(incomplete)]

    return _complete

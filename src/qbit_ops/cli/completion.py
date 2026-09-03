"""Shell-completion callbacks for options whose values are a closed set.

Only an enumeration knowable **without** talking to qBittorrent belongs
here, so this module's completions are pure and network-free by
construction. `--category`, `--tag` and `--tracker` complete too, but
their values live on the instance rather than at declaration time --
that different, weaker guarantee is `qbit_ops.cli.completion_live`, kept
apart so it stays out of this module's own. See
`.agents/specs/list-sort.md` ("--sort" reuses this same mechanism) for
another consumer of this one.
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

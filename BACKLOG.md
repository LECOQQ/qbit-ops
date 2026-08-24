# 🗃️ Backlog

Known issues, deliberately deferred. Each entry says what it is, and
what would bring it back.

An idea that was never a defect does not belong here: a capability goes
to [ROADMAP.md](ROADMAP.md).

> An entry deferred twice becomes a decision: fix it, or accept it with
> a written reason and drop it from this file.

## The command bar wraps instead of truncating

`_format_command_bar` joins entries with no width budget, and both
`#command-bar` and `#footer-row` are `height: auto`.

    120 columns   bar 1 line, footer 2
     80 columns   bar 2 lines, footer 3

On a standard terminal the footer grows and the torrent table loses a
row. `tab_bar.py` already solves the same problem, degrading a string
across four rungs rather than letting it overflow.

**Reopens if** eighty columns is a supported width, or another binding
becomes visible.

## A security test asserts nothing

`tests/test_tracker_security.py::test_print_error_sanitizes_embedded_urls`
calls `print_error` and stops. No `capsys`, no read of `stderr`; it
passes whatever the function does. The behaviour itself was checked and
is correct, so nothing leaks today.

**Reopens** on the next change to `print_error` or to that file.

## The public-surface gate misses a dead `__all__`

`tests/test_qbit_core_public_surface.py` checks that every consumed name
is declared. A module declaring `__all__` twice passes: the second wins,
and if it is the wider one no name is missing.
`features/tracker_status.py` carried two for a while.

**Reopens if** a third dead declaration appears, or when that gate is
next touched.

## A fourth token-normalisation copy stays

`tui/filter_form.py::_csv` shares the strip/blank rule of
`shared/selection.normalize_tokens`, but spans eleven fields instead of
tags alone and does not deduplicate. Merging it would change filter
behaviour nobody asked to change.

**Reopens if** a fifth copy appears, or deduplicating categories and
save paths becomes wanted.

## A clamp whose rationale no longer holds

`tui/formatting.py::_details_dialog_content_width` mirrors an
`#details-dialog` CSS rule that no longer exists; the comment is fixed,
the 86 % fraction and 60-100 clamp are not, because changing them
changes rendering.

**Reopens if** the Details modal looks wrong on a wide terminal.

## A thirty-four line docstring on the public entry point

`qbit_core/__init__.py` carries a runnable usage example, which the
prose budget rules out by default. `docs/ARCHITECTURE.md` points here
rather than duplicating it, and this is the most-read file of the
package.

**Reopens if** `ARCHITECTURE.md` stops pointing here, or the core gets
published documentation of its own.

## Two markers with no referent

`tests/test_tui_bulk_mutation_audit.py` cites `(T-1)`, which no document
defines. `tui/widgets/overview_windows.py` uses an en dash where
`formatting.py` uses an em dash for the same visual meaning.

**Reopens if** anyone knows what `T-1` meant, or the two dashes should
agree.

## Considered, not planned

**A `Result` type across the core.** Three of the four robustness
defects fixed in 0.5.0 were `RecursionError`, `MarkupError` and
`PermissionError` -- raised by the runtime or a library, never values a
`Result` could carry. What caught them was widening an `except`, not
typing a return.

**Ports and adapters for the qBittorrent client.** The ports exist:
eight protocols in `qbit/protocols.py`. Production annotates `client:
Any` in 52 places instead. The work is to use what is there, not to
add a layer.

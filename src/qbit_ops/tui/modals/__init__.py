"""`ModalScreen` subclasses for `qbit_ops.tui`.

Moved out of `qbit_ops.tui.app` (see docs/DECISIONS.md, TUI reorg
phase). Each modal here delegates real actions back to
`QbitOpsTuiApp` via `self.app` (typed only, under `TYPE_CHECKING`, to
avoid a runtime import cycle -- `qbit_ops.tui.app` imports these
modules to construct/push/query the modals). No behavior change: the
delegation calls are identical to their pre-split form, only the
`assert isinstance(self.app, QbitOpsTuiApp)` runtime type-narrowing
idiom is replaced by `typing.cast`, since asserting a concrete type
that only lives in this package's own importer would itself require
importing `qbit_ops.tui.app` at runtime. `self.app` is always the one
`QbitOpsTuiApp` instance in practice (Textual's own contract), so
`cast` changes no observable behavior.
"""

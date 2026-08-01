"""`ModalScreen` subclasses for `qbit_ops.tui`.

Each modal delegates real actions back to `QbitOpsTuiApp` via
`self.app`, typed only under `TYPE_CHECKING` to avoid a runtime import
cycle with `qbit_ops.tui.app` (which imports these modules). Narrowed
with `typing.cast`, not `assert isinstance(...)`, since asserting the
concrete type would itself require the runtime import.
"""

# 📚 `qbit_core` as a library

`qbit_core` is the reusable half of qbit-ops: it talks to qBittorrent and
never prints, prompts or exits. Another Python project can depend on it
without the CLI, the TUI or the MCP server (see
[docs/ARCHITECTURE.md](ARCHITECTURE.md)).

## 🔖 Version

`qbit_core.__version__` is the installed `qbit-ops` distribution version,
or `0+unknown` when the package metadata is absent (for example a bare
copy of `qbit_core/` on `PYTHONPATH`).

## 🧭 Public surface

The public surface is the names listed in `__all__`: in `qbit_core`
itself and in each module below. Anything else, including every
underscore name, is internal.

| Module | Purpose |
|---|---|
| `qbit_core` | `QbitConfig`, `create_qbit_client`, the error classes, `__version__` |
| `qbit_core.instances` | `QbitInstance`, `instances_from_environment`, `validate_instance_name` |
| `qbit_core.features.transfer` | read, change and restore global speed limits and the alternative mode |
| `qbit_core.features.torrents` | bulk pause/resume by hash |
| `qbit_core.qbit.protocols` | the narrow client protocols the functions above are typed against |

`tests/test_qbit_core_public_surface.py` fails when a consumer imports a
name that a module's `__all__` does not declare.

## 🔒 Stability

- Versions follow the qbit-ops releases (SemVer).
- Before 1.0, a minor release may change the public surface; the
  changelog says so under its breaking-change heading.
- A name leaves `__all__` only in a release that announces it.
- Support claims name exact qBittorrent versions
  ([docs/COMPATIBILITY.md](COMPATIBILITY.md)), never a range.

## ⚡ Example: throttle, then give the settings back

```python
import os

from qbit_core import create_qbit_client
from qbit_core.features.transfer import (
    SpeedLimits,
    read_transfer_settings,
    set_alternative_limits,
    set_alternative_mode,
)
from qbit_core.instances import instances_from_environment

instance = instances_from_environment(os.environ)[0]
client = create_qbit_client(instance.config)

before = read_transfer_settings(client)  # before touching anything
throttle = SpeedLimits(download=1024, upload=1024)
set_alternative_limits(client, throttle)
set_alternative_mode(client, True)
...  # playback happens

# Give back only what was changed, and only if it is still as it was
# left: a setting changed by hand in between is someone's decision.
now = read_transfer_settings(client)
if now.alternative_mode and now.alternative_limits == throttle:
    set_alternative_mode(client, before.alternative_mode)
    set_alternative_limits(client, before.alternative_limits)
```

There is no `restore` helper on purpose: rewriting a whole snapshot would
also reset the global limits, and silently overwrite a change made by hand
since the snapshot was taken.

Limits are **bytes per second**, `0` meaning unlimited. qBittorrent
reports an unlimited limit as a negative number on some paths; it is
normalized to `0`. The Web API reference says KiB/s for the preferences,
but every tested version exchanges bytes/s
([docs/COMPATIBILITY.md](COMPATIBILITY.md)). qBittorrent stores whole
KiB: a written value is rounded down to a multiple of 1024 (a non-zero
value below 1024 becomes 1024). Write whole KiB to compare a value read
back with the one written.

## ⏱️ Request timeout

`create_qbit_client(config, request_timeout=15.0)` applies a timeout in
seconds to every request, so a frozen qBittorrent cannot block a
long-running consumer. The default `None` sets none.

## 🖧 Several instances

```text
QBIT_INSTANCES=main,second
QBIT_MAIN_HOST=http://localhost:8080
QBIT_MAIN_USER=admin
QBIT_MAIN_PASSWORD=...
QBIT_SECOND_HOST=...
```

Names are 1 to 32 characters among `a-z`, `0-9`, `-`, `_`; the variable
prefix is the name uppercased with `-` as `_`. Without `QBIT_INSTANCES`,
`QBIT_HOST`, `QBIT_USER` and `QBIT_PASSWORD` give one `default`
instance. `instances_from_environment` takes the mapping as an argument
and reads nothing itself; errors name variables, never their values.

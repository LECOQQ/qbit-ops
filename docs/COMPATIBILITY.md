# 🧩 qBittorrent compatibility

qbit-ops is container-integration tested against these exact versions:

| qBittorrent | Web API | Architecture |
|---|---:|---|
| 4.6.7 | 2.9.3 | amd64 |
| 5.0.0 | 2.11.2 | amd64 |
| 5.1.4 | 2.11.4 | amd64 |
| 5.2.3 | 2.15.1 | amd64 |

This does **not** mean “all qBittorrent versions from 4.6 to 5.2 are supported”. Compatibility claims must always cite exact tested versions -- never a version range.

## 🩺 What `doctor` reports

```bash
qbit-ops doctor
```

`doctor` compares the observed application and Web API versions with the packaged evidence:

- ✅ exact application + Web API match: exact version tested;
- ⚠️ exact application with a different Web API: warning;
- ❔ untested version between known entries: no incompatibility inferred;
- ⚠️ newer or older than the tested evidence: warning;
- 🚫 missing required Web API capability: reported separately.

An untested version is not automatically incompatible.

## 🐢 Speed limits

`qbit_core.features.transfer` reads and writes the global and alternative
speed limits and the alternative mode. On all four versions above, the
`dl_limit`, `up_limit`, `alt_dl_limit` and `alt_up_limit` preferences
are exchanged in bytes/s, not KiB/s as the Web API reference states, and
values are stored in whole KiB. Checked by
`tests/integration/test_matrix_transfer.py`.

## 🔁 Re-run the matrix

```bash
make test-qbit-version QBIT_MATRIX_ID=qbit-5.2.3
make test-qbit-matrix
```

The tests use disposable Docker containers and isolated configuration. The Docker network is dedicated, but public egress is not technically blocked.

The executable source of truth is:

```text
src/qbit_core/data/qbittorrent-matrix.toml
```

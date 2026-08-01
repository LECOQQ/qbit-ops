# 🤝 Contributing

## ⚙️ Setup

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
make install
```

## 🔁 Daily workflow

```bash
poetry run pytest tests/test_doctor.py -k compatibility
make check-fast
make check
```

Useful targets:

```bash
make test-tui
make clean
make test-qbit-version QBIT_MATRIX_ID=qbit-5.2.3
make test-qbit-matrix
```

Docker compatibility tests are opt-in and are not part of normal pull-request CI.

## 📏 Rules worth preserving

- 🧪 Mutating commands stay dry-run by default.
- 🎯 Mutations target canonical hashes, not fuzzy names.
- 🔒 Raw tracker credentials must not appear in ordinary output.
- ♻️ CLI and TUI share feature logic rather than reimplement it.
- 🧩 Compatibility claims remain limited to exact tested evidence.

## 📝 Commits

Use Conventional Commits:

```text
feat: add a feature
fix: correct a bug
refactor: reorganize without behavior changes
docs: update documentation
test: improve tests
```

Run `make check` before opening a pull request.

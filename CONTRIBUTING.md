# Contributing to Annie

Thank you for your interest in contributing! This guide covers everything you need to get started.

---

## Quick start

```bash
git clone https://github.com/fodorad/Annie
cd Annie
uv pip install -e ".[all,dev,docs]"
pre-commit install   # optional: runs ruff automatically before every commit
```

> Annie's core (scanning, parsing, matching, storage, theme, colour) installs with
> just `nicegui` + `pillow` + `numpy`. The `media` extra (`torch`,
> `torchcodec`) is only needed for frame decoding and the render pipeline, and
> requires a system FFmpeg (versions 4–8).

---

## Architecture: layered, import direction enforced

Annie is a single process with strict layer separation. Each layer calls **only**
the layer directly beneath it and never reaches past it:

```
UI layer          annie/app.py, annie/pages/*          (NiceGUI tabs)
   │  calls down only
Service layer     annie/dataset/*, annie/media/*        (scanning, rendering, filtering, …)
   │
Domain layer      annie/core/models.py, annie/parsers/* (pure data, no I/O frameworks)
   │
Infrastructure    annie/core/config.py, annie/core/theme.py,
                  annie/dataset/storage.py (SQLite), annie/media/decode.py (torchcodec)
```

The UI never imports `sqlite3` or `torchcodec` directly — it calls a service
function. Keep it that way: it is what makes the pieces swappable later.

---

## Development workflow

1. **Fork** the repository and create a branch from `main`.
2. **Make your changes** — keep them focused and minimal.
3. **Write or update tests** in `tests/` (the tree mirrors `annie/`).
4. **Run checks locally** before pushing:

   ```bash
   make fix    # auto-format and fix lint issues
   make check  # lint + type-check + tests + docs build (mirrors CI)
   ```

5. **Open a Pull Request** against `main` and fill in the template.

---

## Commit message convention

Annie follows **Conventional Commits** so the version history is readable and the
correct version bump is signalled automatically (via release-please).

| Prefix | Meaning | Version bump |
|--------|---------|--------------|
| `fix:` | Bug fix, regression, hotfix | **Patch** (0.x.y) |
| `feat:` | New feature | **Minor** (0.x.0) |
| `feat!:` or `BREAKING CHANGE:` | API/UI change that breaks existing usage | **Major** (x.0.0) |
| `docs:` | Documentation only | No bump |
| `test:` | Tests only | No bump |
| `refactor:` | Code refactor with no behaviour change | No bump |
| `chore:` | Build, CI, dependency updates | No bump |

### Examples

```
fix: skip macOS AppleDouble (._*) files during scan
feat: add 5-frame strip thumbnails to Browse rows
feat!: rename scan_dataset(annotations_dir=) -> annotations_root=
docs: document protagonist correction flow
chore: bump torchcodec to 0.12 / torch 2.12
```

---

## Release process

Releases are automated with **release-please**. Merging Conventional Commits to
`main` opens/maintains a release PR; merging that PR tags the version, which
triggers the **CD** workflow to build the wheel, publish to PyPI (OIDC trusted
publisher), and create a GitHub Release. Docs deploy on every push to `main`.

---

## Code style

- **Formatter / linter**: [ruff](https://docs.astral.sh/ruff/) — run `make fix`.
- **Type checker**: [ty](https://github.com/astral-sh/ty) — run `make type-check`.
- **Line length**: 100 characters.
- **Python version**: 3.12+.
- **Docstrings**: Google style (rendered by Sphinx autoapi + napoleon).
- **Type hints**: required on public function signatures.

---

## Tests

```bash
make test              # run all tests with coverage
coverage html          # open coverage_html/index.html to browse
```

Tests live in `tests/` and mirror the `annie/` package structure. They use
**synthetic CSV fixtures** generated in `setUp` — they do **not** depend on the
private MOSEI dataset. Tests that need `torchcodec`/FFmpeg skip gracefully when
the `media` extra is not installed.

---

## Reporting bugs and requesting features

Please use the GitHub issue templates:

- **Bug report**: include a minimal reproducible example, Python version, and OS.
- **Feature request**: describe the problem you are trying to solve, not just the solution.

---

## License

By contributing you agree that your work will be released under the [MIT License](LICENSE).

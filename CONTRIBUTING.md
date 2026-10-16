# Contributing

Issues and pull requests are welcome.

## Setup

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## How the code is organised

| Module | Responsibility |
|---|---|
| `model.py` | Reads an ODCS contract and exposes what the platform needs (rules, SLAs, roles, layer) |
| `validate.py` | Lint, breaking-change classification, semantic versioning, dependencies |
| `sqlgen.py` | The SQL templates: pipeline, governance and SLA SQL |
| `bundlegen.py` | Bundle resources: one pipeline and one job per domain |
| `importer.py`, `uc.py` | `import` and `drift`: Unity Catalog metadata to and from contracts |
| `scaffold.py`, `templates/` | `init`: the project template and the example data products |
| `cli.py` | The command line |

## Changing the generated SQL

Generation must stay deterministic: the same contract always gives the same files, which is what
`contractctl check` relies on. The example project is compiled in `tests/test_compile.py` and
compared with `tests/golden/`. When you change a template on purpose:

```bash
UPDATE_GOLDEN=1 pytest tests/test_compile.py
git diff tests/golden/
```

and commit the golden files with the change, so reviewers see the SQL difference.

A change to the generated SQL changes every user's `generated/` folder on upgrade, so mention it
in `CHANGELOG.md`.

## Pull requests

- Add a test for every behaviour change.
- Keep `pytest` and `ruff check .` green on Python 3.10 to 3.13.
- Add an entry under `Unreleased` in `CHANGELOG.md`.

## Releasing

1. Set the version in `pyproject.toml` and move the `Unreleased` entries to a new version in `CHANGELOG.md`.
2. Merge to `main`, then tag: `git tag v0.2.0 && git push origin v0.2.0`.
3. The `release` workflow builds the package and publishes it to PyPI (trusted publishing) and creates a GitHub release.

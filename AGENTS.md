# Working on contractctl (for people and coding agents)

- Source in `src/contractctl/`, tests in `tests/`. Install with `pip install -e ".[dev]"`.
- Before every commit: `pytest` and `ruff check .`. Support Python 3.10+: no syntax newer than 3.10.
- Generated SQL is compared with `tests/golden/`. Never update the golden files to make a test
  pass without understanding the SQL difference; regenerate with `UPDATE_GOLDEN=1 pytest tests/test_compile.py`.
- Generation must stay deterministic (no timestamps, random ids or dict-order dependence).
- Never weaken an access control in the generated SQL (grants, masks, row filters) without an explicit request.
- Files under `src/contractctl/templates/` are copied into user projects by `contractctl init`;
  `{{catalog}}`, `{{bundle_name}}` and `{{version}}` are placeholders.
- Update `CHANGELOG.md` under `Unreleased` for user-visible changes.

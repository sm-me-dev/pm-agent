# Contributing

## Setup

```bash
git clone https://github.com/sm-me-dev/pm-agent.git
cd pm-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest                          # all tests
pytest -x                       # stop on first failure
pytest tests/unit/              # unit tests only
pytest -k "test_discover"       # run by pattern
```

### Coverage

```bash
pytest --cov=src/pm_agent --cov-report=term
```

## Packaging Smoke Test

```bash
make smoke
# or manually:
pip install build && python -m build && pip install dist/*.whl && pm-agent --help
```

## Linting and Type Checking

```bash
ruff check src/                 # lint
mypy src/pm_agent/              # type check
```

Both tools are configured in `pyproject.toml`. CI runs them on every PR.

## Coding Conventions

- Python 3.12+ with `from __future__ import annotations` in every file.
- 100-character line limit.
- Prefer stdlib over new dependencies.
- Type annotations on all public functions.
- Docstrings on public modules and functions.
- Test files mirror the `src/pm_agent/` directory structure.

## Pull Request Process

1. Open an issue first for significant changes.
2. Run the full test suite locally before pushing.
3. Keep PRs focused on a single concern.
4. Update or add tests for changed behavior.
5. Update documentation (README, CHANGELOG) if user-facing.
6. Mark PR as draft until CI is green.

## Releasing

Maintainers:

1. Update `__version__` in `src/pm_agent/__init__.py` and `version` in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Tag: `git tag v<version> && git push origin v<version>`.
4. CI builds and attaches the wheel to the GitHub Release.
5. Install to PyPI: `pip build && twine upload dist/*`.

See `RELEASE.md` for full instructions.

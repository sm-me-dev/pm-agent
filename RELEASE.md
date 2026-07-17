# Release Process

## Prerequisites

- `pip install build twine`
- PyPI API token configured in `~/.pypirc` or `TWINE_PASSWORD` environment variable.
- Write access to the repository.

## Steps

1. **Update version** in two places:
   - `src/pm_agent/__init__.py` — `__version__`
   - `pyproject.toml` — `[project] version`

2. **Update `CHANGELOG.md`** — move items from "Unreleased" to the new version.

3. **Commit and tag**:
   ```bash
   git add -p && git commit -m "release vX.Y.Z"
   git tag vX.Y.Z
   git push origin main --tags
   ```

4. **Build**:
   ```bash
   python -m build
   ```

5. **Upload to PyPI**:
   ```bash
   twine check dist/* && twine upload dist/*
   ```

6. **Create a GitHub Release**:
   - Tag: `vX.Y.Z`
   - Title: `vX.Y.Z`
   - Description: paste the relevant CHANGELOG section
   - Attach: the `.tar.gz` and `.whl` from `dist/`

## Versioning

This project follows [Semantic Versioning](https://semver.org/).
Pre-1.0: minor bumps for feature additions, patch bumps for bug fixes.

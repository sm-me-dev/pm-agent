.PHONY: install test testv lint typecheck coverage smoke clean

install:
	pip install -e ".[dev]" build

test:
	pytest

testv:
	pytest -x -v

lint:
	ruff check src/

typecheck:
	mypy src/pm_agent/

coverage:
	pytest --cov=src/pm_agent --cov-report=term --cov-report=html

smoke:
	python -c "import build; print('build OK')"
	python -m build
	pip install dist/*.whl
	pm-agent --help
	@echo "--- smoke test passed ---"

clean:
	rm -rf build/ dist/ *.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true

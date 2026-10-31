.PHONY: help install install-dev test test-cov lint format clean build publish docs docs-artifacts docs-clean

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the package
	uv sync

install-dev: ## Install the package in development mode
	uv sync --dev

test: ## Run tests
	uv run pytest

test-cov: ## Run tests with coverage
	uv run pytest --cov=src/tilearray --cov-report=html --cov-report=term-missing

lint: ## Run linting
	uv run ruff format --check src tests
	uv run ruff check src tests
	uv run mypy src/tilearray

format: ## Format code
	uv run ruff format src tests
	uv run ruff check --fix src tests

clean: ## Clean build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/

build: ## Build the package
	uv build

docs: ## Build Sphinx HTML docs (no live tile fetches)
	uv sync --extra docs --dev
	uv run --extra docs sphinx-build -W -b html docs docs/_build/html

docs-artifacts: ## Regenerate usage-guide figures via live public hosts
	uv sync --extra docs --dev
	uv run --extra docs python scripts/generate_docs_artifacts.py

docs-clean: ## Remove Sphinx build output
	rm -rf docs/_build docs/api/generated

publish: ## Publish to PyPI (requires PYPI_API_TOKEN)
	uv run twine upload dist/*

pre-commit: ## Install pre-commit hooks
	uv run pre-commit install

pre-commit-run: ## Run pre-commit on all files
	uv run pre-commit run --all-files

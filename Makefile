.PHONY: help install install-dev test test-cov lint format clean build publish

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
	uv run black --check .
	uv run isort --check-only .
	uv run flake8 .
	uv run mypy src/tilearray

format: ## Format code
	uv run black .
	uv run isort .

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

publish: ## Publish to PyPI (requires PYPI_API_TOKEN)
	uv run twine upload dist/*

pre-commit: ## Install pre-commit hooks
	uv run pre-commit install

pre-commit-run: ## Run pre-commit on all files
	uv run pre-commit run --all-files

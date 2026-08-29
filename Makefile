# Attention Sink task runner.
#
# Every target here is the exact command CI runs. If a check passes locally and
# fails in CI, that is a bug in this file, not a fact of life.

SHELL := /bin/bash
.DEFAULT_GOAL := help
.PHONY: help bootstrap format lint typecheck test test-unit test-property \
	test-integration test-web synth dev verify clean

UV ?= uv
NPM ?= npm

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

bootstrap: ## Install every dependency a contributor needs (Python, Node, hooks)
	$(UV) sync --extra dev
	$(NPM) ci
	$(UV) run pre-commit install

format: ## Rewrite code to the project style
	$(UV) run ruff format .
	$(UV) run ruff check --fix .
	$(NPM) run format

lint: ## Check style and lint rules without rewriting anything
	$(UV) run ruff format --check .
	$(UV) run ruff check .
	$(NPM) run lint

typecheck: ## Strict static type checking, Python and TypeScript
	$(UV) run mypy
	$(NPM) run typecheck

test-unit: ## Fast isolated tests of pure components
	$(UV) run pytest tests/unit -m unit

test-property: ## Hypothesis tests asserting invariants over generated inputs
	$(UV) run pytest tests/property -m property

test-integration: ## Tests that cross a process or adapter boundary
	$(UV) run pytest tests/integration -m integration

test: ## All Python tests, with coverage
	$(UV) run pytest tests/unit tests/property tests/integration \
		--cov --cov-report=term-missing --cov-report=xml

test-web: ## TypeScript tests across every workspace (web client and CDK)
	$(NPM) run test --workspaces --if-present

synth: ## Synthesise the CDK app to CloudFormation (no AWS credentials needed)
	$(NPM) run synth --workspace infrastructure/cdk

dev: ## Run the web client locally against fixture data
	AS_RUNTIME_MODE=local $(NPM) run dev --workspace apps/web

verify: lint typecheck test test-web synth ## Everything CI runs, in CI's order
	@echo "verify: all checks passed"

clean: ## Remove build, cache, and coverage artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis .coverage coverage.xml \
		apps/web/dist infrastructure/cdk/cdk.out
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

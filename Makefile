# Attention Sink task runner.
#
# Every target here is the exact command CI runs. If a check passes locally and
# fails in CI, that is a bug in this file, not a fact of life.

SHELL := /bin/bash
.DEFAULT_GOAL := help
.PHONY: help bootstrap format lint typecheck test test-unit test-property \
	test-integration test-contract test-web synth dev verify clean simulate \
	pilot-validate pilot-calibrate pilot-local-validate pilot-draft \
	pilot-local-cycle pilot-local-run pilot-local-export \
	local-db-migrate local-run-create local-cycle local-scheduler local-api \
	local-analyze local-export local-verify local-reset-demo local-all

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

test: ## All Python tests, with coverage, gated per package
	$(UV) run pytest tests/unit tests/property tests/integration \
		--cov --cov-report=term-missing --cov-report=xml
	@# Gated per package, not in aggregate: a well-covered package must not be
	@# allowed to hide an untested one behind a flattering total.
	$(UV) run coverage report --include='packages/domain/*' --fail-under=95
	$(UV) run coverage report --include='packages/policies/*' --fail-under=95
	$(UV) run coverage report --include='packages/model_gateway/*' --fail-under=95
	$(UV) run coverage report --include='packages/pilot/*' --fail-under=95
	$(UV) run coverage report --include='packages/analysis/*' --fail-under=95
	$(UV) run coverage report --include='packages/persistence/*' --fail-under=95
	$(UV) run coverage report --include='packages/api/*' --fail-under=95

simulate: ## Run the policy simulator against a fixture (FIXTURE=path)
	$(UV) run python scripts/simulate_policy.py $(FIXTURE)

# ---------------------------------------------------------------------------
# The pilot. Order matters: validate, calibrate, local-validate, then run. A run
# refuses to start from draft files, and local-validate refuses an uncalibrated
# budget, so the sequence is enforced by the commands rather than by this file.
#
# Nothing here freezes a protocol. FROZEN follows AWS token calibration in Phase 8;
# every run these targets produce is LOCAL_FIXTURE, simulated, and non-canonical.
# ---------------------------------------------------------------------------
PILOT ?= $(UV) run python -m attention_sink.pilot
PILOT_OUT ?= .pilot-runs/local

pilot-validate: ## Check the protocol files agree, and detect any edited after validation
	$(UV) run python scripts/validate_local_protocol.py

pilot-calibrate: ## Measure the seed world locally and write the provisional budget
	$(UV) run python scripts/calibrate_local_budget.py

pilot-local-validate: ## Digest the protocol, mark it LOCAL_VALIDATED, and write the manifest
	$(PILOT) local-validate

pilot-draft: ## Return the protocol to DRAFT so it can be edited
	$(PILOT) draft

pilot-local-cycle: ## Run one fixture cycle across all six arms
	$(UV) run python scripts/run_local_fixture_cycle.py

pilot-local-run: ## Run the full 24-cycle fixture experiment and export it
	$(UV) run python scripts/run_local_fixture_experiment.py --out $(PILOT_OUT)

pilot-local-export: ## Run the full fixture experiment into a chosen directory (PILOT_OUT=path)
	$(UV) run python scripts/run_local_fixture_experiment.py --out $(PILOT_OUT)

# ---------------------------------------------------------------------------
# The persisted local application. SQLite, the local filesystem, fixture models,
# and a local HTTP server. No AWS credential is required by anything below, and
# no AWS service is called: `MODEL_MODE` defaults to fixture and the only
# repository these targets construct is the SQLite one.
# ---------------------------------------------------------------------------
LOCAL ?= $(UV) run python scripts/local_cli.py
LOCAL_DB ?= .pilot-local/pilot.sqlite3
LOCAL_RUN ?= run_local_pilot
LOCAL_EXPORT ?= .pilot-runs/dataset
LOCAL_PORT ?= 8000

local-db-migrate: ## Create the local SQLite database and apply every migration
	$(LOCAL) --database $(LOCAL_DB) migrate

local-run-create: ## Create a run, seed six arms, and interview at cycle 0
	$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) create

local-cycle: ## Advance the run by N cycles (LOCAL_CYCLES=n, default 1)
	$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) cycle --count $(or $(LOCAL_CYCLES),1)

local-status: ## Where the local run has got to
	$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) status

local-scheduler: ## Simulate EventBridge: one cycle per tick until the run completes
	$(UV) run python scripts/run_local_scheduler.py --database $(LOCAL_DB) \
		--run-id $(LOCAL_RUN) --interval $(or $(LOCAL_INTERVAL),0.5)

local-api: ## Serve the local read API on LOCAL_PORT (read-only, no write routes)
	$(UV) run uvicorn --factory attention_sink.api.local:app --port $(LOCAL_PORT)

local-analyze: ## Score every metric and store the evidence
	$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) analyze

local-export: ## Write the complete dataset export and its checksums
	$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) export --out $(LOCAL_EXPORT)

local-verify: ## Check a persisted run against every invariant it claims
	$(UV) run python scripts/verify_local_run.py --database $(LOCAL_DB) \
		--run-id $(LOCAL_RUN) --export $(LOCAL_EXPORT)

local-reset-demo: ## Delete the local run. Refuses anything that is not LOCAL_FIXTURE
	$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) reset

local-all: ## The whole local pipeline, from an empty database to a verified export
	$(MAKE) local-db-migrate local-run-create
	$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) cycle --count 24
	$(MAKE) local-analyze local-export local-verify

test-contract: ## Opt-in contract tests against real Bedrock (costs money, needs credentials)
	AS_BEDROCK_CONTRACT_TESTS=1 $(UV) run pytest tests/integration/test_bedrock_contract.py -m integration

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

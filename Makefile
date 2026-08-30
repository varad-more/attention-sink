# Attention Sink task runner.
#
# Every target here is the exact command CI runs. If a check passes locally and
# fails in CI, that is a bug in this file, not a fact of life.

SHELL := /bin/bash
.DEFAULT_GOAL := help
.PHONY: help bootstrap format lint typecheck test test-unit test-property \
	test-integration test-contract test-web synth dev verify clean simulate \
	pilot-validate pilot-calibrate pilot-local-validate pilot-draft \
	pilot-aws-calibrate pilot-aws-validate pilot-freeze \
	pilot-local-cycle pilot-local-run pilot-local-export \
	local-db-migrate local-run-create local-cycle local-scheduler local-api \
	local-analyze local-export local-verify local-reset-demo local-all \
	pilot-local-demo pilot-local-web pilot-local-e2e pilot-local-build pilot-local-release-check \
	aws-bundle aws-bundle-fast aws-preflight aws-bootstrap-cdk aws-deploy aws-web-build \
	aws-status aws-cycle aws-schedule-inspect aws-schedule-enable aws-schedule-disable \
	aws-invoke-once aws-export aws-smoke aws-destroy aws-outputs aws-bootstrap aws-verify aws-cost \
	aws-execution-inspect aws-execution-enable aws-execution-disable

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
	@# Every directory under tests/, not three named ones. `pytest_collection_modifyitems`
	@# is a session hook, so a conftest anywhere under tests/ is handed every collected
	@# item -- and one that skipped the whole suite went unnoticed here precisely because
	@# this target used to name the directories that conftest did not live in. Collecting
	@# everything means a session-wide skip fails the coverage gates below immediately.
	$(UV) run pytest tests \
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
	$(UV) run coverage report --include='packages/aws/*' --fail-under=95

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

pilot-aws-calibrate: ## Derive the canonical budget from the writer model's own tokeniser
	ALLOW_BEDROCK_CALLS=1 $(BEDROCK_ENV) \
		$(UV) run python scripts/calibrate_aws_budget.py

pilot-aws-validate: ## Mark the calibrated protocol AWS_CALIBRATED and write both manifests
	$(MODEL_ENV) MODEL_MODE=bedrock \
		$(UV) run python scripts/freeze_canonical_protocol.py --status aws_calibrated

pilot-freeze: ## Freeze the calibrated protocol and write the canonical run manifest
	$(MODEL_ENV) MODEL_MODE=bedrock \
		$(UV) run python scripts/freeze_canonical_protocol.py --status frozen

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
	$(UV) run python scripts/verify_run.py --database $(LOCAL_DB) \
		--run-id $(LOCAL_RUN) --export $(LOCAL_EXPORT)

local-reset-demo: ## Delete the local run. Refuses anything that is not LOCAL_FIXTURE
	$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) reset

local-all: ## The whole local pipeline, from an empty database to a verified export
	@# "From an empty database" has to be true on the second run as well, or the
	@# regression suite passes once and then reports a stale run for ever. The reset
	@# refuses anything that is not LOCAL_FIXTURE, so the leading `-` forgives only the
	@# case there was nothing to delete; a refusal still fails `local-run-create` below.
	$(MAKE) local-db-migrate
	-$(MAKE) local-reset-demo
	$(MAKE) local-run-create
	$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) cycle --count 24
	$(MAKE) local-analyze local-export local-verify

# ---------------------------------------------------------------------------
# The local exhibition. One command from an empty checkout to a browsable
# experiment. Everything below is fixture data and says so on every page.
# ---------------------------------------------------------------------------
WEB ?= npm run --workspace apps/web
WEB_PORT ?= 5173

pilot-local-demo: ## Whole product locally: database, run, API, and frontend
	@echo "SIMULATED - LOCAL - NON-CANONICAL. Fixture models; not research results."
	@test -f $(LOCAL_DB) || $(MAKE) local-db-migrate
	@$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) status >/dev/null 2>&1 \
		|| $(MAKE) local-run-create
	@$(LOCAL) --database $(LOCAL_DB) --run-id $(LOCAL_RUN) cycle --count 24
	@$(MAKE) local-analyze
	@echo ""
	@echo "  API      http://localhost:8000/runs/$(LOCAL_RUN)"
	@echo "  Frontend http://localhost:$(WEB_PORT)"
	@echo "  Data     LOCAL_FIXTURE / NON_CANONICAL / SIMULATED_MODEL_OUTPUTS"
	@echo ""
	@$(MAKE) -j2 local-api pilot-local-web

pilot-local-web: ## Frontend dev server against the local API
	$(WEB) dev -- --port $(WEB_PORT) --strictPort

pilot-local-build: ## Production frontend build, plus a typecheck of it
	$(WEB) typecheck
	$(WEB) build

pilot-local-e2e: ## Playwright flows against a freshly built local stack
	$(WEB) e2e

pilot-local-release-check: ## Everything a local release candidate has to pass
	$(MAKE) verify
	$(MAKE) local-all
	$(MAKE) pilot-local-build
	$(MAKE) pilot-local-e2e
	@echo "local release check: all gates passed"

test-contract: ## Opt-in contract tests against real Bedrock (costs money, needs credentials)
	AS_BEDROCK_CONTRACT_TESTS=1 $(UV) run pytest tests/integration/test_bedrock_contract.py -m integration

test-web: ## TypeScript tests across every workspace (web client and CDK)
	$(NPM) run test --workspaces --if-present

synth: aws-bundle-fast ## Synthesise the CDK app to CloudFormation (no AWS credentials needed)
	$(NPM) run synth --workspace infrastructure/cdk

dev: ## Run the web client locally against fixture data
	AS_RUNTIME_MODE=local $(NPM) run dev --workspace apps/web

verify: lint typecheck test test-web synth ## Everything CI runs, in CI's order
	@echo "verify: all checks passed"

clean: ## Remove build, cache, and coverage artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis .coverage coverage.xml \
		apps/web/dist infrastructure/cdk/cdk.out
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

# ---------------------------------------------------------------------------
# AWS. Everything below needs credentials, and everything below is inert until
# an operator arms it: the stack deploys with execution disabled and the
# schedule disabled, in every environment.
#
# The order is fixed and enforced by the commands rather than by this file:
# preflight, bundle, deploy, bootstrap, smoke, and only then a cycle.
# ---------------------------------------------------------------------------
AWS_ENV ?= staging
CDK ?= npx cdk
CDK_DIR ?= infrastructure/cdk
# CDK stops for confirmation on any security-relevant change, which is the right
# default for a human at a terminal and impossible for one without a TTY. Overridable
# per invocation (`make aws-deploy APPROVAL=never`) rather than lowered here.
APPROVAL ?= any-change
STACK ?= AttentionSink-$(AWS_ENV)

# The model set every AWS command uses, in one place so a deployment and the process
# it deploys cannot disagree about which models a run used (ADR-006). Nova Micro is
# the cheapest text model this account can reach; Titan V2 is the embedding model the
# analysis needs. Every one is overridable from the environment, and every one is
# recorded verbatim in the run manifest -- two runs that used different values here
# are different experiments.
AWS_REGION ?= us-east-1
WRITER_MODEL_ID ?= amazon.nova-micro-v1:0
AUDITOR_MODEL_ID ?= amazon.nova-micro-v1:0
JUDGE_MODEL_ID ?= amazon.nova-micro-v1:0
SUMMARY_MODEL_ID ?= amazon.nova-micro-v1:0
EMBEDDING_MODEL_ID ?= amazon.titan-embed-text-v2:0
# `converse` counts by invoking the writer model with its output capped at one token.
# Exact, and valid for a canonical run; see ADR-013 for why `bedrock` is unusable here.
TOKEN_COUNT_SOURCE ?= converse

# The commit a run records as the code that produced it. Resolved here rather than
# inside the application: a process cannot be trusted to know which checkout it was
# started from, and a wrong commit in a manifest is worse than none.
AS_GIT_COMMIT ?= $(shell git rev-parse HEAD 2>/dev/null)

MODEL_ENV = AWS_REGION=$(AWS_REGION) WRITER_MODEL_ID=$(WRITER_MODEL_ID) \
	AUDITOR_MODEL_ID=$(AUDITOR_MODEL_ID) JUDGE_MODEL_ID=$(JUDGE_MODEL_ID) \
	SUMMARY_MODEL_ID=$(SUMMARY_MODEL_ID) EMBEDDING_MODEL_ID=$(EMBEDDING_MODEL_ID) \
	TOKEN_COUNT_SOURCE=$(TOKEN_COUNT_SOURCE) AS_GIT_COMMIT=$(AS_GIT_COMMIT)

# What a process that really invokes a model needs on top of the model set. Never a
# default: a command that spends money says so on its own command line.
BEDROCK_ENV = $(MODEL_ENV) MODEL_MODE=bedrock AS_RUNTIME_MODE=production

# Which deployment an operator command is talking to, and which run in it. Derived
# from AWS_ENV so that one variable selects the stack, its run, and its ceiling
# together -- an operator pointing a command at the wrong deployment is the mistake
# these commands are most able to make.
AS_PILOT_RUN_ID ?= $(if $(filter production,$(AWS_ENV)),run_aws_canonical,run_aws_staging)

# Read from the stack rather than repeated here, so an operator cannot point a
# command at one deployment's run and another's table. Lazily assigned: these shell
# out to CloudFormation, and only the AWS targets ever expand them.
stack_output = $(shell aws cloudformation describe-stacks --stack-name $(STACK) \
	--query "Stacks[0].Outputs[?OutputKey=='$(1)'].OutputValue" --output text 2>/dev/null)
DEPLOY_ENV = AS_DEPLOYMENT_ENVIRONMENT=$(AWS_ENV) AS_PILOT_RUN_ID=$(AS_PILOT_RUN_ID) \
	AS_TABLE_NAME=$(call stack_output,TableName) \
	AS_EXPORT_BUCKET=$(call stack_output,ExportBucketName) \
	AS_RUN_CYCLE_FUNCTION=$(call stack_output,RunCycleFunctionName) \
	AS_API_URL=$(call stack_output,ApiUrl) \
	AS_CLOUDFRONT_URL=$(call stack_output,CloudFrontUrl)

OPERATOR ?= $(BEDROCK_ENV) $(DEPLOY_ENV) $(UV) run python scripts/aws_cli.py

aws-bundle: ## Build the deployable Python package, with dependencies vendored
	$(UV) run python scripts/build_lambda_bundle.py

aws-bundle-fast: ## Build the bundle without dependencies: enough to synthesise, not to deploy
	@$(UV) run python scripts/build_lambda_bundle.py --no-deps

aws-preflight: ## Verify account, Region, models, and that nothing is armed
	$(OPERATOR) preflight

aws-bootstrap-cdk: ## Bootstrap CDK in this account and Region (once per account)
	cd $(CDK_DIR) && $(CDK) bootstrap

# Two passes, and both are necessary. The exhibition is compiled against the API's
# URL and the API answers the exhibition's origin, and neither exists until the stack
# does. Pass one creates them; pass two supplies each to the other.
aws-deploy: aws-preflight aws-bundle ## Deploy, rebuild the exhibition against the API, deploy again
	cd $(CDK_DIR) && $(MODEL_ENV) $(CDK) deploy $(STACK) -c environment=$(AWS_ENV) --require-approval $(APPROVAL)
	@$(MAKE) aws-web-build
	AS_ALLOWED_ORIGINS=$$(aws cloudformation describe-stacks --stack-name $(STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CloudFrontUrl'].OutputValue" --output text) \
		bash -c 'cd $(CDK_DIR) && $(MODEL_ENV) $(CDK) deploy $(STACK) -c environment=$(AWS_ENV) \
		--require-approval $(APPROVAL)'
	@$(MAKE) aws-outputs

aws-web-build: ## Build the exhibition against the deployed API. No fixture data.
	@$(eval AS_API_URL := $(shell aws cloudformation describe-stacks --stack-name $(STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text))
	@$(eval AS_RUN_ID := $(shell aws cloudformation describe-stacks --stack-name $(STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='PilotRunId'].OutputValue" --output text))
	@test -n "$(AS_API_URL)" || (echo "no ApiUrl output; deploy the stack first" && exit 1)
	VITE_API_BASE_URL=$(AS_API_URL) VITE_PUBLIC_RUN_ID=$(AS_RUN_ID) \
		VITE_DEPLOYMENT_MODE=$(AWS_ENV) VITE_FIXTURE_MODE=false \
		$(WEB) build

aws-outputs: ## Print the deployment outputs an operator needs
	@aws cloudformation describe-stacks --stack-name $(STACK) \
		--query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table

# Pass `BOOTSTRAP_ARGS=--interview` to take the cycle-0 interviews at the same time.
# Not the default: they cost six model calls, and creating a run should not spend.
BOOTSTRAP_ARGS ?=

aws-bootstrap: ## Create the deployed run: six identical seeds, cycle 0, nothing generated
	$(OPERATOR) bootstrap $(BOOTSTRAP_ARGS)

aws-status: ## Where the deployed run has got to
	$(OPERATOR) status

aws-cycle: ## Advance the deployed run by one cycle, from this process
	$(OPERATOR) cycle

aws-execution-inspect: ## Whether the deployed function may advance the run
	$(OPERATOR) execution inspect

aws-execution-enable: ## Arm the deployed function. It can then spend on model calls.
	$(OPERATOR) execution enable

aws-execution-disable: ## Disarm the deployed function
	$(OPERATOR) execution disable

aws-schedule-inspect: ## What the schedule is set to, and whether it is armed
	$(OPERATOR) schedule inspect

# `make aws-schedule-enable EVERY='rate(5 minutes)'` arms it at a different cadence.
EVERY ?=

aws-schedule-enable: ## Arm the schedule. Refuses unless the function is armed too.
	$(OPERATOR) schedule enable $(if $(EVERY),--every '$(EVERY)')

aws-schedule-disable: ## Disarm the schedule
	$(OPERATOR) schedule disable

aws-invoke-once: ## Fire the deployed run-cycle function once, exactly as the schedule would
	$(OPERATOR) schedule invoke-once

aws-export: ## Write the complete dataset to the export bucket
	$(OPERATOR) export

aws-cost: ## Report what the deployed run spent, and estimate what that costs
	$(DEPLOY_ENV) $(UV) run python scripts/cost_report.py --run-id $(AS_PILOT_RUN_ID) \
		--stack $(STACK) $(if $(PRICES),--prices $(PRICES))

aws-verify: ## Check the deployed run against every invariant it claims to satisfy
	$(DEPLOY_ENV) $(UV) run python scripts/verify_run.py --source aws \
		--run-id $(AS_PILOT_RUN_ID) $(if $(EXPORT_DIR),--export $(EXPORT_DIR))

aws-smoke: ## Real Bedrock smoke tests. Costs money. Needs ALLOW_BEDROCK_CALLS=1.
	ALLOW_BEDROCK_CALLS=1 $(BEDROCK_ENV) $(UV) run pytest tests/smoke -m smoke -v

aws-destroy: ## Tear the stack down. Refuses production, which retains its data.
	@test "$(AWS_ENV)" != "production" || (echo "refusing to destroy production" && exit 1)
	cd $(CDK_DIR) && $(CDK) destroy $(STACK) -c environment=$(AWS_ENV)

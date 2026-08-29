# The model gateway

Every model interaction in this system goes through a typed protocol in
`packages/model_gateway`. Nothing else may call a provider. This document is the
reference for what those protocols are, what the models are asked, what is recorded,
and how it fails.

Two decisions from the ADRs shape everything here. [ADR-004](adr/004-policy-blind-writer-and-evaluator.md):
no prompt may name the mechanism under study. [ADR-010](adr/010-opaque-memory-labels-in-prompts.md):
models refer to memories by per-request labels, never by their real identifiers,
because a real identifier reads `mem_arm_fifo_000007` and names the mechanism in
every prompt of every cycle.

## The seven roles

| Protocol            | Method                                     | Model                | Returns                                                                                          |
| ------------------- | ------------------------------------------ | -------------------- | ------------------------------------------------------------------------------------------------ |
| `ThoughtWriter`     | `write`                                    | `WRITER_MODEL_ID`    | `WriterResult` — the entry, the candidate memory, claimed citations resolved to real identifiers |
| `CitationAuditor`   | `audit`                                    | `AUDITOR_MODEL_ID`   | `AuditResult` — support level and both evidence spans per claim, plus unsupported claims         |
| `MemorySummarizer`  | `summarize`                                | `SUMMARY_MODEL_ID`   | `SummaryResult` — the summary, its sources, its measured token cost                              |
| `Interviewer`       | `interview`                                | `WRITER_MODEL_ID`    | `InterviewResult` — one answer per question, with citations and stated uncertainty               |
| `ClaimEvaluator`    | `evaluate`                                 | `JUDGE_MODEL_ID`     | `EvaluationJudgment` — a categorical verdict, a score, evidence, and three versions              |
| `EmbeddingProvider` | `embed`                                    | `EMBEDDING_MODEL_ID` | `EmbeddingResult` — a typed record, and whether it had to be computed                            |
| `ExactTokenCounter` | `count`, `count_detailed`, `count_request` | `WRITER_MODEL_ID`    | `TokenCount` — an exact count and the call that produced it                                      |

The interviewer uses the writer's model deliberately: the interview asks the same
agent to answer questions about its own world, and a different model would be a
different subject. The token counter uses the writer's model because the budget it
measures is the writer's context.

Each protocol has exactly one implementation pair. Five text roles share
`StructuredCaller`, which is provider-agnostic; below it sits one class,
`StrandsInvoker`, which is the only code in the repository that reaches a model.
Fixture mode replaces that one class and runs the same adapters.

## What the writer sees, and what it does not

The writer is given the cycle number, this cycle's stimulus, and the active memories
in presentation order, each under a label such as `[m1]`. It is given nothing else.

Structurally excluded, rather than merely omitted:

- **The arm and the policy.** Memories are relabelled per request, so no identifier
  carries the arm. `assert_policy_blind` then rejects any rendered prompt containing
  an arm identifier, a policy version string, or mechanism vocabulary — checked on
  every request, including every retry.
- **Retired memories.** `present_memories` refuses a memory that has left the active
  set. Only the evaluator may be shown retired records, and only because noticing
  that a passage still echoes one is a judgement the protocol asks for.
- **Anything policy-visible about a memory.** Only the text is rendered. Token cost,
  citation count, discounted score, birth cycle, and status all stay behind.
- **Other arms, later stimuli, predictions, and metrics.** None of them is a
  parameter of any request builder, so there is no path for them to arrive.

Everything a request carries sits inside a boundary whose token is a digest of that
data. The system instruction says the region is recorded material and not
instruction, so text that tries to close the boundary and issue orders would need a
64-bit partial preimage of its own digest.

## Prompts

Prompts are files under `packages/model_gateway/attention_sink/model_gateway/prompt_templates/<name>/<version>.txt`,
shipped inside the package so a deployed artefact cannot carry a different version
from the digest its manifest records. Each file holds the static system instruction,
a `--- USER ---` separator, and the data template, whose `$fields` are substituted
with request data only.

A prompt file is immutable for the life of its version. A change is a new version.

| Prompt                  | SHA-256 of the file                                                       |
| ----------------------- | ------------------------------------------------------------------------- |
| `writer/v1`             | `sha256:fac3f188c701f87dfeb00c85ed68472758282ce99603939532032edd9b064c31` |
| `citation-auditor/v1`   | `sha256:49b0ec80cf038f09fe40a39824e979ec09e4379c2564ec36132ef587058514b0` |
| `summarizer/v1`         | `sha256:5dffbba1fe970803adf8b4fb0d848168787b0ab2627e0b42217f39692830463e` |
| `interview/v1`          | `sha256:ae32b71c3e4aef5185ad2f5884c92a1519eab3bae46a7b39df95e522496b09a5` |
| `truth-evaluator/v1`    | `sha256:86906d3cc8eaf1eae164613d87f5aec20b2f1e3a94f7fa4724063ba774aba6c5` |
| `summary-entailment/v1` | `sha256:8e857655566f88678cc0175b5a41e481fecd5156ad11c96e6a20bf227e0dd3be` |

Prompt set digest (`v1`): `sha256:741a27e46422f22364d97e4ac7b76fb430c5686926e6784461a443416d421144`

This table is checked against the shipped files by
`tests/unit/test_prompt_digests.py`, so a prompt edited without a version bump fails
a test rather than silently invalidating every digest a run recorded.

No prompt asks for reasoning. Each ends by saying to return the named fields and
nothing else, and the schemas have no field a chain of thought could occupy.

## Output schemas

Every response is a strict Pydantic model: unknown keys forbidden, every string
bounded, every categorical answer drawn from a closed vocabulary. A response that
does not fit is retried or surfaced, never coerced.

| Schema             | Fields                                                                                                                   |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| `ThoughtOutput`    | `journal_entry`, `candidate_memory`, `claimed_citations`, `explicit_belief_claims`, `uncertainty_notes`                  |
| `AuditOutput`      | `audited_citations` (`memory_ref`, `support_level`, `memory_evidence_span`, `entry_evidence_span`), `unsupported_claims` |
| `SummaryOutput`    | `summary_text`, `source_memory_refs`, `preserved_fact_statements`, `omitted_fact_statements`, `uncertainty_statements`   |
| `InterviewOutput`  | `answers` (`question_id`, `answer`, `cited_memory_refs`, `stated_uncertainty`)                                           |
| `EvaluationOutput` | `task`, `label`, `score`, `evidence_memory_refs`, `supporting_excerpts`                                                  |

Checks the schema cannot express are applied by the adapter, and a failure earns
another attempt with a hint saying what to fix:

- Every label a response cites was in the request it answered.
- An audit returns one entry per claim, in the order the claims were given.
- An audit's `memory_evidence_span`, for anything above `NONE`, appears in the memory
  it cites, compared with whitespace collapsed and case folded. A fabricated
  quotation would let an invented citation move a real policy statistic.
- A summary names exactly the plan's sources, in the plan's order, and costs no more
  than the plan's ceiling once counted.
- An interview answers each question once, in the order asked.
- A judgement answers the task it was given, in that task's vocabulary.

## Evaluation tasks

| Task                           | Verdicts                                    | Prompt                  |
| ------------------------------ | ------------------------------------------- | ----------------------- |
| `origin_recall`                | `present`, `partial`, `absent`              | `truth-evaluator/v1`    |
| `canonical_fact_contradiction` | `contradicted`, `consistent`, `unaddressed` | `truth-evaluator/v1`    |
| `summary_entailment`           | `entailed`, `partial`, `unsupported`        | `summary-entailment/v1` |
| `unavailable_record_echo`      | `echo`, `paraphrase`, `none`                | `truth-evaluator/v1`    |

The last is `EvaluationTask.GRAVEYARD_ECHO` internally. Its model-facing value is
neutral on purpose: a judge told it was looking for echoes of _discarded_ memories
would know memories are discarded, and would score against that expectation.

Every judgement carries the evaluator model identifier, the prompt version, and
`EVALUATION_CALCULATION_VERSION` — three things that can each move a score
independently. `EvaluationJudgment.as_metric_evidence` renders it as the domain's
`MetricEvidence`, with a rationale assembled from the verdict and the quoted
excerpts rather than generated a second time.

## Citations

Only `CitationSource.WRITER` citations may change memory state; the domain enforces
that. Within writer citations, only those at an accepted support level count.
`FULL` is accepted by default and `PARTIAL` is not: a partially supported citation is
real evidence about the writing and no evidence that the memory was load-bearing, so
counting it would inflate exactly the signal the citation-weighted arm rests on. A run
may accept `PARTIAL` by configuring `accepted_levels`.

Rejected citations are kept, not discarded. How often an arm claims memories it did
not use is a finding about that arm.

Interview citations are recorded for analysis and can never update a statistic. An
interview is a measurement, and a measurement that changed what an arm went on to
remember would be measuring itself.

## Configuration

| Variable                  | Default   | Meaning                           |
| ------------------------- | --------- | --------------------------------- |
| `MODEL_MODE`              | `fixture` | `bedrock` or `fixture`            |
| `AWS_REGION`              | none      | Required in `bedrock` mode        |
| `WRITER_MODEL_ID`         | none      | Required in `bedrock` mode        |
| `AUDITOR_MODEL_ID`        | none      | Required in `bedrock` mode        |
| `SUMMARY_MODEL_ID`        | none      | Required in `bedrock` mode        |
| `JUDGE_MODEL_ID`          | none      | Required in `bedrock` mode        |
| `EMBEDDING_MODEL_ID`      | none      | Required in `bedrock` mode        |
| `WRITER_TEMPERATURE`      | `0.7`     | Applied identically to every arm  |
| `WRITER_TOP_P`            | `0.9`     | Applied identically to every arm  |
| `WRITER_MAX_TOKENS`       | `1024`    | Ceiling on a generated response   |
| `SUMMARY_MAX_TOKENS`      | `256`     | Ceiling on a generated summary    |
| `REQUEST_TIMEOUT_SECONDS` | `60`      | Read and connect timeout          |
| `MAX_MODEL_RETRIES`       | `3`       | Retries _after_ the first attempt |

Model identifiers and the Region have no defaults, per
[ADR-006](adr/006-model-ids-from-configuration.md): a vendor can change what an
unspecified model resolves to, silently making two runs incomparable. Inference
parameters do have defaults, because a number this repository chooses cannot drift
underneath a run and is recorded on every call and in the settings record.

Resolution fails closed. `MODEL_MODE=bedrock` without a Region and all five model
identifiers refuses to start; an unparseable or out-of-range number refuses to start;
and `AS_RUNTIME_MODE=production` with `MODEL_MODE=fixture` refuses to start, checked
both in `GatewaySettings.from_env` and again in `build_gateway`.

## Token counting

Production counts with the writer model's own tokeniser through Bedrock
`CountTokens`, version `bedrock-count-tokens-v1`. See
[ADR-011](adr/011-exact-token-counts-in-production.md). Counts are cached on
`(model_id, content_hash)`; blank text costs nothing and makes no call. There is no
fallback to the heuristic — a production process whose counter is unavailable stops.

`heuristic-v1` remains for isolated tests and local fixture mode, reached by
configuration and never by degradation.

## Embeddings

Titan Text Embeddings V2, with configurable dimensions (256, 512, or 1024) and
normalisation requested from the model rather than applied afterwards. An
`EmbeddingRecord` carries the model identifier, dimensions, input hash, vector,
normalisation flag, timestamp, and schema version — and not the text, which the
memory already holds. Identity is `(model_id, input_hash)`, so the same text embedded
twice by one model is one record.

## Failures and retries

| Code                          | Retried          | Typical cause                                     |
| ----------------------------- | ---------------- | ------------------------------------------------- |
| `validation_error`            | no               | Malformed request, or an unrecognised 4xx         |
| `access_denied`               | no               | Credentials or policy                             |
| `throttling`                  | yes              | Rate or quota                                     |
| `model_timeout`               | yes              | Read timeout, or the model timed out              |
| `transient_server_error`      | yes              | 5xx, connection failure, model not ready          |
| `unsupported_model`           | no               | The configured model does not exist here          |
| `malformed_structured_output` | yes              | The response did not fit its schema or its checks |
| `token_limit_exceeded`        | only as a repair | Request too long, or a summary over its ceiling   |

`token_limit_exceeded` is the interesting one. Reported by the provider it means the
request was too long, and an identical retry would be too long again. Raised by the
summariser it means the next attempt can be told to write less, so it arrives as a
`SchemaRepairNeeded`, which is retryable by virtue of carrying a repair.

An exception this module does not recognise is re-raised unchanged rather than
labelled with the nearest-looking code. A wrong error code in a run's record is worse
than an unfamiliar traceback, because it is the one a reader will believe.

Backoff is bounded exponential with full jitter — a uniform draw across the whole
window, because six arms are invoked from one orchestration and a fixed delay would
turn one throttle into a synchronised second one. Botocore's own retries are disabled
so the recorded retry count is the real one.

## Metadata

Every call returns a `CallMetadata`, and a failed call carries one on its exception:
role, model identifier, Region, outcome, latency, retry count, `simulated`, and where
available the request identifier, input and output tokens, prompt version, prompt
hash, stop reason, and terminal error code.

`request_id_of` is the only place a provider response is read for metadata, and it
takes one field. The rest of `ResponseMetadata` is HTTP headers, and headers carry
authorization material that must never reach a log.

The prompt hash covers the template digest and both rendered turns, so identical
inputs give an identical hash and two calls that should have been the same call look
the same in the record.

## Fixture mode

`MODEL_MODE=fixture` needs no AWS account and no network. It is not a second gateway:
`FixtureInvoker` substitutes for the one class that speaks to a provider, and the
ordinary adapters run above it unchanged — same prompts, same blindness guard, same
label resolution, same retries, same metadata.

Responses are derived from the rendered request, so a local run is reproducible, and
the fixture reads the request back through the same module that wrote it, so a change
to a prompt's layout cannot leave the fake answering the old one.

Everything it produces is marked. Text carries `[simulated]`, metadata carries
`simulated=True`, and the fixture evaluator returns the null verdict of its task with
a score of zero, so a fabricated judgement can never read as a finding.

`tests/integration/test_fixture_cycle.py` drives a complete cycle this way: write,
audit, fold citations into the arm's statistics, plan a compression, write the
summary, commit it, then interview, judge, and embed the result.

## Contract tests

`tests/integration/test_bedrock_contract.py` runs against real Bedrock and is skipped
unless `AS_BEDROCK_CONTRACT_TESTS=1`. It checks the contract, not the content: that
the configured models accept the schemas this package sends, that a response
validates, that the token counter answers, and that the embedding model returns the
dimensions it was asked for. Run with `make test-contract`. It costs money.

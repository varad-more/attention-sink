# Phase 3 - Model gateway and schema-validated AI interactions

## Plan

- [x] Dependencies: `boto3`, `strands-agents`, `boto3-stubs` behind an `aws` extra.
- [x] `settings.py`: `ModelMode` (bedrock|fixture), `WriterInference`, `GatewaySettings`
      reading every env var in the brief; fail closed in bedrock mode; refuse fixture
      mode inside a production runtime.
- [x] `prompts.py` + `prompt_templates/<name>/v1.txt` for the six prompts, each loaded
      with a SHA-256 digest.
- [x] `schemas.py`: strict Pydantic models for every model-facing input and output.
- [x] `rendering.py`: opaque per-request memory labels, the untrusted-content fence,
      and the policy-blind guard that runs on every rendered prompt.
- [x] `failures.py`: eight error codes, classification, bounded backoff with jitter.
- [x] `observability.py`: `CallMetadata` for every call.
- [x] `interfaces.py`: the seven gateway protocols and their result records.
- [x] `adapters.py`: five role adapters over one provider seam; `StrandsInvoker` below it.
- [x] `tokens.py`: Bedrock `CountTokens`, cached on (model id, content hash).
- [x] `embeddings.py`: Titan Text Embeddings V2, deduplicated on (model id, hash).
- [x] `fixtures.py`: one deterministic invoker plus a counter and an embedding provider.
- [x] `factory.py`: mode-driven construction; deleted the untyped Phase 1 fixture gateway.
- [x] Tests: the thirteen listed cases, plus opt-in Bedrock contract tests.
- [x] Docs: prompt-version table, ADR-010 (opaque refs), ADR-011 (exact token counts),
      implementation status.

## Review

**The module named in the plan as `metadata.py` shipped as `observability.py`.**
`CallMetadata` needs `ModelErrorCode`, and the classifier needs `ModelRole`, so
splitting the vocabulary from the record would have made the two modules import each
other. One module holds what every call reports; the other holds the rules that
decide which code an exception earns.

**The plan did not anticipate that memory identifiers leak the arm.** `mem_arm_fifo_000007`
names the mechanism, and the writer has to be given a handle for every memory it may
cite. Per-request labels (ADR-010) were the answer, and they made the blindness guard
possible: with no legitimate use for an arm identifier in a prompt, it can be banned
outright.

**`bedrock.py` became `adapters.py`.** Once fixture mode ran through the same five
adapters with a different invoker, only one class in the module was Bedrock-specific,
and the file name was claiming more than it did.

**One thing was deleted rather than kept.** `FixtureModelGateway` answered requests no
protocol had shaped and returned values no schema had validated. Keeping it alongside
the typed gateway would have contradicted acceptance criterion 1.

**Two branches turned out to be reachable that looked defensive.** `Retrier`'s final
`AssertionError` fires for a hand-built `RetryPolicy(max_attempts=0)`, and the fence
guard's rejection is testable once separated from the digest that makes reaching it
require a partial preimage. Both are now tested rather than excused.

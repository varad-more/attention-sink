"""Turning experiment state into a prompt, without telling the model what it is.

Two guarantees are enforced here rather than reviewed for.

The first is that a model never learns which mechanism governs the arm it is writing
for. That is ADR-004, and it has a subtlety: a real memory identifier reads
``mem_arm_fifo_000007``, so simply listing memories by identifier would name the
policy in every prompt. Memories are therefore presented under per-request labels --
``m1``, ``m2`` -- that mean nothing outside the request. See ADR-010.

The second is that recorded material is never read as instruction. Every prompt puts
its data inside a boundary whose token is derived from the data itself, so text that
tries to close the boundary and issue instructions would have to contain a preimage
of its own digest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from attention_sink.domain import ArmId, Memory, MemoryId, content_hash
from attention_sink.model_gateway.prompts import (
    DEFAULT_PROMPT_VERSION,
    PromptLibrary,
    PromptName,
    PromptTemplate,
)
from attention_sink.model_gateway.schemas import (
    ClaimedCitation,
    EvaluationTask,
    InterviewQuestion,
)

__all__ = [
    "BANNED_POLICY_VERSIONS",
    "MemoryPresentation",
    "ModelRequest",
    "PromptLeakError",
    "UnknownMemoryReferenceError",
    "assert_policy_blind",
    "build_auditor_request",
    "build_evaluation_request",
    "build_interview_request",
    "build_summarizer_request",
    "build_writer_request",
    "parse_claims",
    "parse_memory_block",
    "parse_questions",
    "present_memories",
]

_EMPTY_BLOCK = "(none)"
_FENCE_LENGTH = 16

_MEMORY_LINE = re.compile(r"^\[(m[0-9]+)\] (.*)$", re.MULTILINE)
_CLAIM_LINE = re.compile(
    r"^- (m[0-9]+) is said to support: (.*) \(entry span: (.*)\)$", re.MULTILINE
)
_QUESTION_LINE = re.compile(r"^- ([a-z][a-z0-9_]*): (.*)$", re.MULTILINE)

BANNED_POLICY_VERSIONS: tuple[str, ...] = (
    "fifo-v1",
    "lru-v1",
    "heavy-v1",
    "sink-v1",
    "random-v1",
    "summary-v1",
    "full-v1",
    "stateless-v1",
)
"""Policy version strings that must never reach a model.

Listed literally because this package must not import ``attention_sink.policies`` --
an adapter that depended on the mechanism could come to serve it. The list is
cross-checked against the real registry by ``tests/unit/test_prompt_blindness.py``,
so a new policy version that nobody added here fails a test rather than leaking.
"""

_BANNED_PHRASES: tuple[str, ...] = (
    r"\barm_[a-z_]+\b",
    r"\bfifo\b",
    r"\blru\b",
    r"\bleast[ -]recently[ -](?:used|cited)\b",
    r"\bheavy[ -]hitter\b",
    r"\battention sink\b",
    r"\bsliding window\b",
    r"\bpinned origin\b",
    r"\bmemory policy\b",
    r"\beviction policy\b",
    r"\brebalance\b",
    r"\brecency reserve\b",
    r"\bretention density\b",
    r"\bgraveyard\b",
)
"""Vocabulary that would name the mechanism, as word-boundary patterns.

Deliberately not a list of every word that touches memory. A guard broad enough to
match ordinary English would fire on recorded material and take a run down for no
reason; these are phrases that would be strange in a journal and decisive in a
prompt. ``arm_[a-z_]+`` covers every current and future neutral arm identifier
without this module having to track the enum.
"""

_BANNED = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        *_BANNED_PHRASES,
        *(re.escape(version) for version in BANNED_POLICY_VERSIONS),
        *(re.escape(arm.value) for arm in ArmId),
    )
)


class PromptLeakError(RuntimeError):
    """A prompt contains something no model may see.

    Covers both halves of the guarantee: vocabulary that would name the mechanism,
    and memories that have left the active set and must not be shown to a writer.
    """


class UnknownMemoryReferenceError(ValueError):
    """A model cited a label that was not in the request it answered."""


def assert_policy_blind(text: str, *, where: str) -> None:
    """Refuse text that names the mechanism under study.

    The failure names the matched tokens and where they were found, never the text
    they were found in: a prompt carries recorded material, and an error message is
    a log line.

    Raises:
        PromptLeakError: The text contains banned vocabulary.
    """
    hits = sorted(
        {match.group(0).lower() for pattern in _BANNED for match in pattern.finditer(text)}
    )
    if hits:
        msg = f"{where} would disclose the mechanism under study: {', '.join(hits)}"
        raise PromptLeakError(msg)


@dataclass(frozen=True, slots=True)
class MemoryPresentation:
    """The memories one request showed, and the labels it showed them under.

    The map is per request and is not stored anywhere: a label is meaningful only
    while the response to that request is being resolved.
    """

    refs: tuple[str, ...]
    memory_ids: tuple[MemoryId, ...]
    texts: tuple[str, ...]
    block: str

    def resolve(self, ref: str) -> MemoryId:
        """Return the real identifier behind a label the model used.

        Raises:
            UnknownMemoryReferenceError: The label was not in this request.
        """
        try:
            return self.memory_ids[self.refs.index(ref)]
        except ValueError as exc:
            offered = ", ".join(self.refs) or "none"
            msg = f"model cited {ref!r}, which was not supplied; it was given: {offered}"
            raise UnknownMemoryReferenceError(msg) from exc

    def resolve_all(self, refs: Iterable[str]) -> tuple[MemoryId, ...]:
        """Resolve every label, rejecting the whole set if any is unknown.

        Raises:
            UnknownMemoryReferenceError: Any label was not in this request.
        """
        return tuple(self.resolve(ref) for ref in refs)

    def text_for(self, ref: str) -> str:
        """Return the text shown under a label, for verifying a quoted span.

        Raises:
            UnknownMemoryReferenceError: The label was not in this request.
        """
        try:
            return self.texts[self.refs.index(ref)]
        except ValueError as exc:
            msg = f"model cited {ref!r}, which was not supplied"
            raise UnknownMemoryReferenceError(msg) from exc


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """One fully rendered call: both turns, and the map back to real identifiers."""

    prompt: PromptTemplate
    system: str
    user: str
    presentation: MemoryPresentation

    @property
    def prompt_hash(self) -> str:
        """Digest of the template and both rendered turns.

        Identical inputs give an identical hash, so two calls that should have been
        the same call are visibly the same in the recorded metadata.
        """
        return content_hash(f"{self.prompt.digest}\n{self.system}\n{self.user}")


def present_memories(
    memories: Sequence[Memory], *, require_active: bool = True
) -> MemoryPresentation:
    """Label memories ``m1..mn`` in the order given and render them as a block.

    Only the text is rendered. Token cost, citation count, birth cycle, status, and
    the real identifier all stay behind, because each of them would let a model infer
    something about the mechanism from the material it is meant to be reasoning over.

    Args:
        memories: What to show, in presentation order.
        require_active: Refuse memories that have left the active set. True for
            every prompt a writer, auditor, summariser, or interviewer sees. False
            only for an evaluator, whose task can be precisely to notice retired
            material in a passage.

    Raises:
        PromptLeakError: ``require_active`` is set and a memory has been retired.
    """
    if require_active:
        retired = [memory.memory_id for memory in memories if not memory.is_active]
        if retired:
            msg = f"{len(retired)} retired memories would be shown to a model"
            raise PromptLeakError(msg)

    refs = tuple(f"m{index}" for index in range(1, len(memories) + 1))
    texts = tuple(memory.text for memory in memories)
    block = (
        "\n".join(f"[{ref}] {text}" for ref, text in zip(refs, texts, strict=True)) or _EMPTY_BLOCK
    )
    return MemoryPresentation(
        refs=refs,
        memory_ids=tuple(memory.memory_id for memory in memories),
        texts=texts,
        block=block,
    )


def _fence(parts: Sequence[str]) -> str:
    """Derive the boundary token for one request from the data it carries.

    Deterministic, so a replayed request renders identically, and unguessable from
    inside the data, because closing the boundary would require a 64-bit partial
    preimage of the digest of the very text doing the guessing.

    Raises:
        PromptLeakError: The data already contains its own boundary token.
    """
    token = content_hash("\n".join(parts)).removeprefix("sha256:")[:_FENCE_LENGTH]
    _require_boundary_absent(token, parts)
    return token


def _require_boundary_absent(token: str, parts: Sequence[str]) -> None:
    """Refuse data that already contains the token meant to bound it.

    Split out from :func:`_fence` so the rejection can be tested. Triggering it
    through :func:`_fence` would mean finding a partial preimage, which is what makes
    the guard worth having and what makes it untestable from the outside.

    Raises:
        PromptLeakError: A part contains ``token``.
    """
    if any(token in part for part in parts):
        msg = "recorded material contains the boundary token derived from it"
        raise PromptLeakError(msg)


def _request(
    template: PromptTemplate, presentation: MemoryPresentation, **fields: object
) -> ModelRequest:
    """Render both turns and refuse the result if it names the mechanism."""
    user = template.render_user(**fields)
    assert_policy_blind(template.system, where=f"prompt {template.identifier} system turn")
    assert_policy_blind(user, where=f"prompt {template.identifier} data turn")
    return ModelRequest(
        prompt=template, system=template.system, user=user, presentation=presentation
    )


def build_writer_request(
    prompts: PromptLibrary,
    *,
    cycle: int,
    stimulus_text: str,
    active_memories: Sequence[Memory],
    version: str = DEFAULT_PROMPT_VERSION,
) -> ModelRequest:
    """Render the one call that produces an arm's thought for a cycle.

    The writer receives the cycle number, this cycle's stimulus, and the active
    memories. Not the arm, not the policy, not another arm's output, not a later
    stimulus, not a metric, and not a memory that has been retired.

    Raises:
        PromptLeakError: A retired memory or banned vocabulary reached the prompt.
    """
    template = prompts.load(PromptName.WRITER, version)
    presentation = present_memories(active_memories)
    fence = _fence([stimulus_text, presentation.block])
    return _request(
        template,
        presentation,
        cycle=cycle,
        stimulus=stimulus_text,
        memory_block=presentation.block,
        fence=fence,
    )


def build_auditor_request(
    prompts: PromptLibrary,
    *,
    journal_entry: str,
    candidate_memory: str,
    claims: Sequence[ClaimedCitation],
    active_memories: Sequence[Memory],
    version: str = DEFAULT_PROMPT_VERSION,
) -> ModelRequest:
    """Render the call that checks whether a thought rests on what it claims.

    The auditor sees the writing and the memories and nothing about where either
    came from, so it cannot score an arm's expected behaviour instead of its text.

    Raises:
        PromptLeakError: A retired memory or banned vocabulary reached the prompt.
    """
    template = prompts.load(PromptName.CITATION_AUDITOR, version)
    presentation = present_memories(active_memories)
    rendered_claims = (
        "\n".join(
            f"- {claim.memory_ref} is said to support: {claim.supported_statement} "
            f"(entry span: {claim.journal_span})"
            for claim in claims
        )
        or _EMPTY_BLOCK
    )
    fence = _fence([journal_entry, candidate_memory, rendered_claims, presentation.block])
    return _request(
        template,
        presentation,
        journal_entry=journal_entry,
        candidate_memory=candidate_memory,
        claims=rendered_claims,
        memory_block=presentation.block,
        fence=fence,
    )


def build_summarizer_request(
    prompts: PromptLibrary,
    *,
    sources: Sequence[Memory],
    summary_token_limit: int,
    version: str = DEFAULT_PROMPT_VERSION,
) -> ModelRequest:
    """Render the call that writes one summary for a plan the policy already made.

    The sources are exactly the memories the ``CompressionPlan`` named. The model
    chooses the words; it was never asked which memories to lose.

    Raises:
        PromptLeakError: A retired memory or banned vocabulary reached the prompt.
    """
    template = prompts.load(PromptName.SUMMARIZER, version)
    presentation = present_memories(sources)
    fence = _fence([presentation.block])
    return _request(
        template,
        presentation,
        summary_token_limit=summary_token_limit,
        memory_block=presentation.block,
        fence=fence,
    )


def build_interview_request(
    prompts: PromptLibrary,
    *,
    questions: Sequence[InterviewQuestion],
    active_memories: Sequence[Memory],
    stimulus_text: str | None = None,
    version: str = DEFAULT_PROMPT_VERSION,
) -> ModelRequest:
    """Render the fixed question set against what an arm currently holds.

    ``stimulus_text`` is supplied only where the protocol says the interview happens
    with this cycle's event in view. Everywhere else the interview is a probe of
    memory alone.

    Raises:
        PromptLeakError: A retired memory or banned vocabulary reached the prompt.
    """
    template = prompts.load(PromptName.INTERVIEW, version)
    presentation = present_memories(active_memories)
    rendered_questions = (
        "\n".join(f"- {question.question_id}: {question.text}" for question in questions)
        or _EMPTY_BLOCK
    )
    stimulus_section = "" if stimulus_text is None else f"\nEvent of this cycle:\n{stimulus_text}\n"
    fence = _fence([rendered_questions, stimulus_section, presentation.block])
    return _request(
        template,
        presentation,
        questions=rendered_questions,
        stimulus_section=stimulus_section,
        memory_block=presentation.block,
        fence=fence,
    )


def build_evaluation_request(
    prompts: PromptLibrary,
    *,
    task: EvaluationTask,
    passage: str,
    reference_statements: Sequence[str],
    records: Sequence[Memory] = (),
    version: str = DEFAULT_PROMPT_VERSION,
) -> ModelRequest:
    """Render one structured judgement.

    ``records`` may include retired memories: detecting that a passage still echoes
    material an arm no longer holds is precisely one of the judgements asked for, and
    it cannot be made without showing the judge the material.

    Raises:
        PromptLeakError: Banned vocabulary reached the prompt.
    """
    name = (
        PromptName.SUMMARY_ENTAILMENT
        if task is EvaluationTask.SUMMARY_ENTAILMENT
        else PromptName.TRUTH_EVALUATOR
    )
    template = prompts.load(name, version)
    presentation = present_memories(records, require_active=False)
    rendered_references = "\n".join(f"- {statement}" for statement in reference_statements) or (
        _EMPTY_BLOCK
    )
    fence = _fence([passage, rendered_references, presentation.block])
    return _request(
        template,
        presentation,
        task=task.value,
        passage=passage,
        references=rendered_references,
        memory_block=presentation.block,
        fence=fence,
    )


# The three readers below exist so that the local fixture invoker can answer a request
# without a second definition of how a request looks. They parse exactly what the
# builders above wrote, in the same module, so the two cannot drift apart. They are a
# convenience for a deterministic fake, never a parser for provider output.


def parse_memory_block(user: str) -> tuple[tuple[str, str], ...]:
    """Recover the ``(label, text)`` pairs a rendered data turn presented."""
    return tuple((match.group(1), match.group(2)) for match in _MEMORY_LINE.finditer(user))


def parse_claims(user: str) -> tuple[tuple[str, str, str], ...]:
    """Recover the ``(label, statement, span)`` triples an auditor turn carried."""
    return tuple(
        (match.group(1), match.group(2), match.group(3)) for match in _CLAIM_LINE.finditer(user)
    )


def parse_questions(user: str) -> tuple[tuple[str, str], ...]:
    """Recover the ``(question_id, text)`` pairs an interview turn asked."""
    return tuple((match.group(1), match.group(2)) for match in _QUESTION_LINE.finditer(user))

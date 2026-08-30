"""Running every analysis over a persisted run, and keeping what it concluded.

Reads through the repository protocol and writes back through it, so the same service
runs locally on SQLite and later in a Lambda on DynamoDB. It owns the ordering --
recall before drift, Graveyard before echo, because echo needs to know what was really
lost -- and nothing else.

Every score is stored as ``MetricEvidence``: the value, the versions of the evaluator
and the calculation, the memories the judgement rested on, and a rationale in words.
A metric row that could not be argued with would not be worth storing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from attention_sink.analysis.contradiction import ContradictionFinding, analyse_contradictions
from attention_sink.analysis.echo import EchoMeasurement, measure_echo
from attention_sink.analysis.graveyard import GraveyardEntry, build_graveyard
from attention_sink.analysis.metrics import (
    METRIC_VERSION,
    QuestionScore,
    cosine_distance,
    identity_document,
    pairwise_distance_matrix,
    recall_averages,
    score_origin_recall,
    secondary_metrics,
)
from attention_sink.domain import ArmId, MetricEvidence, version_token
from attention_sink.model_gateway import EvaluationTask, ModelGateway
from attention_sink.pilot import ArmCycleSnapshot
from attention_sink.pilot.protocol import ProtocolBundle
from attention_sink.pilot.repositories import (
    AnalysisStatus,
    PilotRepository,
    RunRecord,
    StoredInterview,
)

__all__ = ["AnalysisResult", "AnalysisService"]

ORIGIN_RECALL = "origin_recall"
IDENTITY_DRIFT = "identity_drift"
GRAVEYARD_ECHO = "graveyard_echo"
CONTRADICTION = "contradiction_rate"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Everything one pass over a run concluded."""

    run_id: str
    metrics: tuple[MetricEvidence, ...]
    graveyard: tuple[GraveyardEntry, ...]
    echoes: tuple[EchoMeasurement, ...]
    contradictions: tuple[ContradictionFinding, ...]
    divergence: dict[str, dict[str, dict[str, float]]]
    """Cycle to the symmetric pairwise identity-distance matrix at that cycle."""

    question_scores: tuple[QuestionScore, ...] = ()


@dataclass
class AnalysisService:
    """Computes and persists every metric the pilot defines."""

    repository: PilotRepository
    bundle: ProtocolBundle
    gateway: ModelGateway
    clock: Callable[[], datetime] = _utc_now
    _embedding_cache: dict[str, tuple[float, ...]] = field(default_factory=dict, repr=False)

    # --------------------------------------------------------------- embedding

    def embed(self, run_id: str, text: str) -> tuple[float, ...]:
        """Embed text, reusing the stored vector when there is one.

        Cached in the store as well as in memory, so re-running analysis on a
        finished run costs nothing and produces the same numbers.
        """
        from attention_sink.domain import content_hash

        key = f"text:{content_hash(text)}"
        if key in self._embedding_cache:
            return self._embedding_cache[key]
        stored = self.repository.get_embedding(run_id, key=key)
        raw = None if stored is None else stored.get("vector")
        if isinstance(raw, list):
            vector = tuple(float(value) for value in raw)
        else:
            result = self.gateway.embeddings.embed(text)
            vector = tuple(result.record.vector)
            self.repository.store_embedding(
                run_id,
                key=key,
                record={
                    "vector": list(vector),
                    "model_id": result.record.model_id,
                    "input_hash": result.record.input_hash,
                },
            )
        self._embedding_cache[key] = vector
        return vector

    def _evaluate(
        self, task: EvaluationTask
    ) -> Callable[[str, Sequence[str]], tuple[str, float, str]]:
        """A judgement callable bound to one task, for the paths that may ask."""

        def call(passage: str, statements: Sequence[str]) -> tuple[str, float, str]:
            judgment = self.gateway.evaluator.evaluate(
                task=task, passage=passage, reference_statements=list(statements)
            )
            return judgment.output.label, judgment.output.score, judgment.evaluator_model_id

        return call

    # ---------------------------------------------------------------- the pass

    def analyse_run(self, run_id: str) -> AnalysisResult:
        """Compute every metric for ``run_id`` and store what it concluded.

        Raises:
            ValueError: No such run.
        """
        run = self.repository.get_run(run_id)
        if run is None:
            msg = f"no run {run_id}"
            raise ValueError(msg)

        metrics: list[MetricEvidence] = []
        graveyard: list[GraveyardEntry] = []
        echoes: list[EchoMeasurement] = []
        contradictions: list[ContradictionFinding] = []
        scores: list[QuestionScore] = []

        by_arm = {
            arm: self.repository.list_arm_snapshots(run_id, arm_id=arm)
            for arm in run.configuration.arms
        }
        for snapshots in by_arm.values():
            graveyard.extend(build_graveyard(run_id, snapshots))

        metrics.extend(self._secondary(run, by_arm))
        recall_metrics, question_scores = self._origin_recall(run)
        metrics.extend(recall_metrics)
        scores.extend(question_scores)
        drift_metrics, divergence = self._identity_drift(run)
        metrics.extend(drift_metrics)
        echo_metrics, measurements = self._echo(run, by_arm, graveyard)
        metrics.extend(echo_metrics)
        echoes.extend(measurements)
        contradiction_metrics, findings = self._contradictions(run)
        metrics.extend(contradiction_metrics)
        contradictions.extend(findings)

        for metric in metrics:
            self.repository.store_metric(metric)
        self.repository.store_analysis_status(
            AnalysisStatus(
                run_id=run_id,
                analysis_name="all",
                metric_version=METRIC_VERSION,
                completed_cycles=self.repository.list_completed_cycles(run_id),
                updated_at=self.clock(),
            )
        )
        return AnalysisResult(
            run_id=run_id,
            metrics=tuple(metrics),
            graveyard=tuple(graveyard),
            echoes=tuple(echoes),
            contradictions=tuple(contradictions),
            divergence=divergence,
            question_scores=tuple(scores),
        )

    # ------------------------------------------------------------- components

    def _secondary(
        self, run: RunRecord, by_arm: dict[ArmId, tuple[ArmCycleSnapshot, ...]]
    ) -> list[MetricEvidence]:
        """Every deterministic metric, for every arm, at the run's current cycle."""
        metrics: list[MetricEvidence] = []
        usage = run.usage
        for arm, snapshots in by_arm.items():
            state = self.repository.get_current_arm_state(run.run_id, arm_id=arm)
            if state is None:
                continue
            computed = secondary_metrics(
                state,
                budget_tokens=run.configuration.memory_budget_tokens,
                snapshots=snapshots,
                cumulative_calls=usage.total_calls,
                cumulative_input_tokens=usage.input_tokens,
                cumulative_output_tokens=usage.output_tokens,
            )
            for name, value in computed.as_dict().items():
                metrics.append(
                    self._evidence(
                        run,
                        arm_id=arm,
                        cycle=run.current_cycle,
                        name=name,
                        value=0.0 if value is None else float(value),
                        rationale=f"deterministic {name} at cycle {run.current_cycle}",
                    )
                )
        return metrics

    def _origin_recall(self, run: RunRecord) -> tuple[list[MetricEvidence], list[QuestionScore]]:
        """Score every checkpoint interview against the canonical record."""
        metrics: list[MetricEvidence] = []
        scores: list[QuestionScore] = []
        evaluate = self._evaluate(EvaluationTask.ORIGIN_RECALL)
        for interview in self.repository.get_interviews(run.run_id):
            per_question = score_origin_recall(
                interview,
                protocol=self.bundle.interview,
                ledger=self.bundle.truth_ledger,
                evaluate=evaluate,
            )
            scores.extend(per_question)
            unweighted, weighted = recall_averages(per_question)
            matched = tuple(fact for score in per_question for fact in score.matched_fact_ids)
            for name, value in (
                (ORIGIN_RECALL, unweighted),
                (f"{ORIGIN_RECALL}_weighted", weighted),
            ):
                metrics.append(
                    self._evidence(
                        run,
                        arm_id=interview.arm_id,
                        cycle=interview.cycle,
                        name=name,
                        value=value,
                        cited=interview.reported_memory_ids,
                        rationale=(
                            f"{len(per_question)} factual questions; matched facts "
                            f"{sorted(set(matched)) or 'none'}"
                        ),
                    )
                )
        return metrics, scores

    def _identity_drift(
        self, run: RunRecord
    ) -> tuple[list[MetricEvidence], dict[str, dict[str, dict[str, float]]]]:
        """Distance from each arm's cycle-0 identity, and the pairwise matrix."""
        metrics: list[MetricEvidence] = []
        divergence: dict[str, dict[str, dict[str, float]]] = {}
        interviews = self.repository.get_interviews(run.run_id)
        by_cycle: dict[int, dict[ArmId, StoredInterview]] = {}
        for interview in interviews:
            by_cycle.setdefault(interview.cycle, {})[interview.arm_id] = interview
        if 0 not in by_cycle:
            return metrics, divergence

        baseline = {
            arm: self.embed(run.run_id, identity_document(interview))
            for arm, interview in by_cycle[0].items()
        }
        for cycle in sorted(by_cycle):
            vectors = {
                arm.value: self.embed(run.run_id, identity_document(interview))
                for arm, interview in by_cycle[cycle].items()
            }
            divergence[str(cycle)] = pairwise_distance_matrix(vectors)
            for arm, interview in by_cycle[cycle].items():
                if arm not in baseline:
                    continue
                distance = cosine_distance(baseline[arm], vectors[arm.value])
                metrics.append(
                    self._evidence(
                        run,
                        arm_id=arm,
                        cycle=cycle,
                        name=IDENTITY_DRIFT,
                        value=distance,
                        cited=interview.reported_memory_ids,
                        rationale=(
                            f"cosine distance of the cycle-{cycle} identity document from "
                            f"the cycle-0 one, over questions "
                            f"{', '.join(_identity_question_ids())}"
                        ),
                    )
                )
        return metrics, divergence

    def _echo(
        self,
        run: RunRecord,
        by_arm: dict[ArmId, tuple[ArmCycleSnapshot, ...]],
        graveyard: Sequence[GraveyardEntry],
    ) -> tuple[list[MetricEvidence], list[EchoMeasurement]]:
        """Measure every candidate memory against what its arm can no longer see."""
        metrics: list[MetricEvidence] = []
        measurements: list[EchoMeasurement] = []
        evaluate = self._evaluate(EvaluationTask.GRAVEYARD_ECHO)
        for arm, snapshots in by_arm.items():
            entries = [entry for entry in graveyard if entry.arm_id is arm]
            state = self.repository.get_current_arm_state(run.run_id, arm_id=arm)
            if state is None:
                continue
            for snapshot in snapshots:
                available = [entry for entry in entries if entry.retirement_cycle < snapshot.cycle]
                if not available:
                    continue
                active = [
                    memory
                    for memory in state.memories
                    if memory.memory_id in set(snapshot.active_memory_ids_before)
                ]
                measurement = measure_echo(
                    run_id=run.run_id,
                    arm_id=arm,
                    cycle=snapshot.cycle,
                    memory_id=snapshot.candidate_memory_id,
                    text=snapshot.candidate_memory,
                    graveyard=available,
                    active=active,
                    embed=lambda text: self.embed(run.run_id, text),
                    evaluate=evaluate,
                )
                measurements.append(measurement)
                metrics.append(
                    self._evidence(
                        run,
                        arm_id=arm,
                        cycle=snapshot.cycle,
                        name=GRAVEYARD_ECHO,
                        value=measurement.echo_delta,
                        cited=tuple(
                            i for i in (measurement.nearest_forgotten_memory_id,) if i is not None
                        ),
                        rationale=(
                            f"{measurement.category.value}: forgotten "
                            f"{measurement.forgotten_similarity:.3f} minus active "
                            f"{measurement.active_similarity:.3f}"
                        ),
                    )
                )
        return metrics, measurements

    def _contradictions(
        self, run: RunRecord
    ) -> tuple[list[MetricEvidence], list[ContradictionFinding]]:
        """Classify every checkpoint answer, and score the contradiction rate."""
        metrics: list[MetricEvidence] = []
        findings: list[ContradictionFinding] = []
        question_facts = {
            question.question_id: tuple(question.fact_ids)
            for question in self.bundle.interview.questions
        }
        evaluate = self._evaluate(EvaluationTask.CANONICAL_FACT_CONTRADICTION)
        for interview in self.repository.get_interviews(run.run_id):
            classified = analyse_contradictions(
                interview,
                ledger=self.bundle.truth_ledger,
                question_facts=question_facts,
                evaluate=evaluate,
            )
            findings.extend(classified)
            contradictory = sum(
                1 for finding in classified if finding.label.value.endswith("contradiction")
            )
            considered = [f for f in classified if f.label.value != "not_applicable"]
            metrics.append(
                self._evidence(
                    run,
                    arm_id=interview.arm_id,
                    cycle=interview.cycle,
                    name=CONTRADICTION,
                    value=contradictory / len(considered) if considered else 0.0,
                    cited=interview.reported_memory_ids,
                    rationale=(
                        f"{contradictory} contradictory of {len(considered)} applicable "
                        f"answers; uncertainty is never counted as contradiction"
                    ),
                )
            )
        return metrics, findings

    # ------------------------------------------------------------------ helper

    def _evidence(
        self,
        run: RunRecord,
        *,
        arm_id: ArmId,
        cycle: int,
        name: str,
        value: float,
        rationale: str,
        cited: Sequence[str] = (),
    ) -> MetricEvidence:
        """One stored score, with the versions and the memories behind it."""
        return MetricEvidence(
            run_id=run.run_id,
            arm_id=arm_id,
            cycle=cycle,
            metric_name=name,
            value=value,
            evaluator_version=_evaluator_version(self.gateway),
            calculation_version=METRIC_VERSION,
            cited_memory_ids=tuple(dict.fromkeys(cited)),
            rationale=rationale,
            computed_at=self.clock(),
        )


def _identity_question_ids() -> tuple[str, ...]:
    from attention_sink.analysis.metrics import IDENTITY_QUESTION_IDS

    return IDENTITY_QUESTION_IDS


def _evaluator_version(gateway: ModelGateway) -> str:
    """The evaluator a metric was scored under, recorded even when it was not asked.

    Transliterated, because a vendor model identifier is not a version string:
    ``amazon.nova-lite-v1:0`` carries a colon and ``MetricEvidence.evaluator_version``
    is a ``Version``. The run manifest keeps the identifier verbatim; this is the form
    that fits in the field, and it stays legible enough to match the two up.
    """
    settings: Any = gateway.settings
    models = settings.models
    if models is None:
        return "fixture-evaluator-v1"
    return version_token(str(models.judge_model_id))

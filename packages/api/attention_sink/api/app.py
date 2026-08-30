"""The local read API: the same read services a Lambda will wrap, over HTTP.

Read-only by construction rather than by policy. There is no write route, no mutating
verb is registered, and a test asserts that the route table contains only GET. Local
administrative actions -- creating a run, advancing a cycle, resetting the demo -- stay
on the command line, because an endpoint that could advance the experiment is an
endpoint that could advance it twice.

Three things are filtered out of every response, and each of them would be a leak of a
different kind:

- **prepared cycles**, which describe a cycle that has not happened yet
- **future stimuli**, which would let a reader see what the arms are about to face
- **evaluator notes and truth-ledger metadata**, which say what an answer is *for*

Prompt *text* is hidden too; prompt versions and hashes are published, because those
are what makes a run reproducible without publishing the apparatus itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from attention_sink.analysis import build_graveyard, lineage_of
from attention_sink.api.schemas import (
    ApiEnvelope,
    ArmSummary,
    CycleView,
    GraveyardView,
    InterviewView,
    Page,
    RunSummary,
)
from attention_sink.domain import ArmId
from attention_sink.pilot.protocol import ProtocolBundle
from attention_sink.pilot.repositories import PilotRepository, RunRecord
from attention_sink.protocol import current_version

__all__ = ["build_app"]

DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)
"""Where the local exhibition is served from.

The frontend runs on its own port, so every request it makes is cross-origin and a
browser will discard an otherwise-successful response without these headers. An
explicit list rather than a wildcard: the API is read-only, but "read-only" is not a
reason to let any page on the internet read a run.
"""

MAX_PAGE_SIZE = 200
"""Nothing returns an unbounded list. A run has 144 snapshots today and a later one
may have thousands, and an endpoint that returned all of them would work until it
did not."""


def _etag(value: str) -> str:
    """A strong ETag for an immutable record, from the digest it already carries."""
    return f'"{value}"'


def _immutable(response: Response, digest: str) -> None:
    """Mark a response as an immutable record and give it its ETag.

    Committed snapshots and stored interviews never change, so they can be cached
    indefinitely. Everything else is a projection of a moving run and is not.
    """
    response.headers["ETag"] = _etag(digest)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"


def _mutable(response: Response) -> None:
    """Mark a response as a view of a run that is still moving."""
    response.headers["Cache-Control"] = "no-cache"


def build_app(
    repository: PilotRepository,
    bundle: ProtocolBundle | None = None,
    *,
    allowed_origins: Sequence[str] = DEFAULT_ALLOWED_ORIGINS,
) -> FastAPI:
    """Build the read API over one repository.

    Args:
        repository: The store to read. Never written to by any route here.
        bundle: Accepted and deliberately unused. Every stimulus the API publishes
            comes from a committed snapshot, which already carries the one stimulus
            that arm was shown; reading the deck here is exactly how a future
            stimulus would leak, so the deck is not consulted at all.
        allowed_origins: Browser origins permitted to read this API.
    """
    del bundle
    version = current_version()
    app = FastAPI(
        title="Attention Sink read API",
        summary="Completed cycles, arms, graveyard, interviews, and metrics.",
        version=version.app_version,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET"],
        allow_headers=["accept", "content-type"],
        expose_headers=["ETag"],
    )

    def _run_or_404(run_id: str) -> RunRecord:
        run = repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id}")
        return run

    def _completed_or_404(run: RunRecord, cycle: int) -> int:
        """Refuse any cycle the run has not committed.

        The single check that keeps the future of the experiment private. A cycle
        that has been prepared but not committed is indistinguishable, from out here,
        from one that has not been generated at all.
        """
        if cycle < 1 or cycle > run.current_cycle:
            raise HTTPException(
                status_code=404,
                detail=f"cycle {cycle} of {run.run_id} is not a completed cycle",
            )
        return cycle

    # ------------------------------------------------------------------ meta

    @app.get("/health")
    def health() -> dict[str, str]:
        """Whether the process is up. Says nothing about the data."""
        return {"status": "ok"}

    @app.get("/version")
    def api_version() -> dict[str, str | None]:
        """What is running, so a reader can tie a response to a commit."""
        return {
            "app_version": version.app_version,
            "git_commit": version.git_commit,
            "mode": "local_fixture",
        }

    # ------------------------------------------------------------------ runs

    @app.get("/runs")
    def list_runs() -> ApiEnvelope[list[RunSummary]]:
        """Every run, newest first."""
        return ApiEnvelope(data=[RunSummary.of(run) for run in repository.list_runs()])

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, response: Response) -> ApiEnvelope[RunSummary]:
        """One run's public head."""
        run = _run_or_404(run_id)
        _mutable(response)
        return ApiEnvelope(data=RunSummary.of(run))

    @app.get("/runs/{run_id}/arms")
    def list_arms(run_id: str, response: Response) -> ApiEnvelope[list[ArmSummary]]:
        """Every arm's current public state."""
        run = _run_or_404(run_id)
        states = repository.get_all_current_arm_states(run_id)
        _mutable(response)
        return ApiEnvelope(
            data=[
                ArmSummary.of(arm, states[arm.value], run)
                for arm in run.configuration.arms
                if arm.value in states
            ]
        )

    @app.get("/runs/{run_id}/arms/{arm_id}")
    def get_arm(run_id: str, arm_id: str, response: Response) -> ApiEnvelope[ArmSummary]:
        """One arm's current public state."""
        run = _run_or_404(run_id)
        arm = _arm_or_404(arm_id)
        state = repository.get_current_arm_state(run_id, arm_id=arm)
        if state is None:
            raise HTTPException(status_code=404, detail=f"no state for {arm_id} in {run_id}")
        _mutable(response)
        return ApiEnvelope(data=ArmSummary.of(arm, state, run))

    # ---------------------------------------------------------------- cycles

    @app.get("/runs/{run_id}/cycles")
    def list_cycles(
        run_id: str,
        response: Response,
        limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> ApiEnvelope[Page[int]]:
        """Every completed cycle number. Never a prepared one."""
        _run_or_404(run_id)
        completed = repository.list_completed_cycles(run_id)
        _mutable(response)
        return ApiEnvelope(data=Page.of(completed, limit=limit, offset=offset))

    @app.get("/runs/{run_id}/cycles/{cycle}")
    def get_cycle(run_id: str, cycle: int, response: Response) -> ApiEnvelope[list[CycleView]]:
        """Every arm's record for one completed cycle."""
        run = _run_or_404(run_id)
        _completed_or_404(run, cycle)
        snapshots = repository.list_cycle_snapshots(run_id, cycle=cycle)
        if not snapshots:
            raise HTTPException(status_code=404, detail=f"cycle {cycle} has no snapshots")
        _immutable(response, snapshots[0].snapshot_hash)
        by_arm = {snapshot.arm_id: snapshot for snapshot in snapshots}
        return ApiEnvelope(
            data=[CycleView.of(by_arm[arm]) for arm in run.configuration.arms if arm in by_arm]
        )

    # -------------------------------------------------------------- graveyard

    @app.get("/runs/{run_id}/graveyard")
    def get_graveyard(
        run_id: str,
        response: Response,
        arm_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> ApiEnvelope[Page[GraveyardView]]:
        """What each arm has lost, derived from its committed snapshots."""
        run = _run_or_404(run_id)
        arms = [_arm_or_404(arm_id)] if arm_id else list(run.configuration.arms)
        entries = [
            GraveyardView.of(entry)
            for arm in arms
            for entry in build_graveyard(run_id, repository.list_arm_snapshots(run_id, arm_id=arm))
        ]
        _mutable(response)
        return ApiEnvelope(data=Page.of(entries, limit=limit, offset=offset))

    @app.get("/runs/{run_id}/graveyard/{memory_id}")
    def get_graveyard_entry(
        run_id: str, memory_id: str, response: Response
    ) -> ApiEnvelope[GraveyardView]:
        """One lost memory, and what became of it."""
        run = _run_or_404(run_id)
        for arm in run.configuration.arms:
            for entry in build_graveyard(run_id, repository.list_arm_snapshots(run_id, arm_id=arm)):
                if entry.memory_id == memory_id:
                    _immutable(response, entry.snapshot_evidence)
                    return ApiEnvelope(data=GraveyardView.of(entry))
        raise HTTPException(status_code=404, detail=f"no retired memory {memory_id} in {run_id}")

    # ------------------------------------------------------------- interviews

    @app.get("/runs/{run_id}/interviews")
    def list_interviews(run_id: str, response: Response) -> ApiEnvelope[list[InterviewView]]:
        """Every stored checkpoint interview."""
        _run_or_404(run_id)
        _mutable(response)
        return ApiEnvelope(data=[InterviewView.of(i) for i in repository.get_interviews(run_id)])

    @app.get("/runs/{run_id}/interviews/{cycle}")
    def get_interviews(
        run_id: str, cycle: int, response: Response
    ) -> ApiEnvelope[list[InterviewView]]:
        """One checkpoint's interviews, across all six arms."""
        run = _run_or_404(run_id)
        if cycle > run.current_cycle:
            raise HTTPException(status_code=404, detail=f"cycle {cycle} is not yet completed")
        stored = repository.get_interviews(run_id, cycle=cycle)
        if not stored:
            raise HTTPException(status_code=404, detail=f"no interviews at cycle {cycle}")
        _immutable(response, stored[0].record_hash)
        return ApiEnvelope(data=[InterviewView.of(i) for i in stored])

    # ---------------------------------------------------------------- metrics

    @app.get("/runs/{run_id}/metrics")
    def get_metrics(
        run_id: str,
        response: Response,
        metric_name: str | None = None,
        arm_id: str | None = None,
        limit: int = Query(default=200, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> ApiEnvelope[Page[dict[str, Any]]]:
        """Stored metric evidence, with the versions that produced each score."""
        _run_or_404(run_id)
        metrics = repository.get_metrics(
            run_id,
            metric_name=metric_name,
            arm_id=_arm_or_404(arm_id) if arm_id else None,
        )
        _mutable(response)
        return ApiEnvelope(
            data=Page.of(
                [metric.model_dump(mode="json") for metric in metrics], limit=limit, offset=offset
            )
        )

    @app.get("/runs/{run_id}/divergence")
    def get_divergence(run_id: str, response: Response) -> ApiEnvelope[dict[str, Any]]:
        """The pairwise identity-distance matrix at each checkpoint.

        Geometric distance between two identity documents. It says the answers moved
        apart; it does not say why, and nothing downstream should read it as cause.
        """
        _run_or_404(run_id)
        stored = repository.get_analysis_artifact(run_id, name="divergence")
        _mutable(response)
        return ApiEnvelope(data=stored or {"matrices": {}})

    @app.get("/runs/{run_id}/echoes")
    def get_echoes(
        run_id: str,
        response: Response,
        arm_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> ApiEnvelope[Page[dict[str, Any]]]:
        """Measured resemblances between new memories and forgotten ones.

        A measurement, not a claim. A positive delta means the new text sits closer
        to something the arm can no longer see than to anything it can; it does not
        mean the arm reached the forgotten record.
        """
        _run_or_404(run_id)
        stored = repository.get_analysis_artifact(run_id, name="echoes") or {"items": []}
        items = [item for item in stored["items"] if arm_id is None or item["arm_id"] == arm_id]
        _mutable(response)
        return ApiEnvelope(data=Page.of(items, limit=limit, offset=offset))

    @app.get("/runs/{run_id}/contradictions")
    def get_contradictions(
        run_id: str,
        response: Response,
        cycle: int | None = None,
        limit: int = Query(default=200, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> ApiEnvelope[Page[dict[str, Any]]]:
        """How each checkpoint answer stood against the canonical record."""
        _run_or_404(run_id)
        stored = repository.get_analysis_artifact(run_id, name="contradictions") or {"items": []}
        items = [item for item in stored["items"] if cycle is None or item["cycle"] == cycle]
        _mutable(response)
        return ApiEnvelope(data=Page.of(items, limit=limit, offset=offset))

    @app.get("/runs/{run_id}/question-scores")
    def get_question_scores(
        run_id: str, response: Response, cycle: int | None = None
    ) -> ApiEnvelope[list[dict[str, Any]]]:
        """Per-question Origin Recall scores, with what matched and how."""
        _run_or_404(run_id)
        stored = repository.get_analysis_artifact(run_id, name="question_scores") or {"items": []}
        _mutable(response)
        del cycle
        return ApiEnvelope(data=list(stored["items"]))

    @app.get("/runs/{run_id}/lineage/{memory_id}")
    def get_lineage(
        run_id: str, memory_id: str, response: Response
    ) -> ApiEnvelope[dict[str, list[str]]]:
        """One memory's parents and descendants."""
        run = _run_or_404(run_id)
        for arm in run.configuration.arms:
            snapshots = repository.list_arm_snapshots(run_id, arm_id=arm)
            lineage = lineage_of(memory_id, snapshots)
            if lineage["parents"] or lineage["children"]:
                _mutable(response)
                return ApiEnvelope(data=lineage)
        raise HTTPException(status_code=404, detail=f"no lineage for {memory_id} in {run_id}")

    @app.get("/runs/{run_id}/exports")
    def list_exports(run_id: str, response: Response) -> ApiEnvelope[list[dict[str, Any]]]:
        """Every dataset export recorded for this run."""
        _run_or_404(run_id)
        _mutable(response)
        return ApiEnvelope(
            data=[
                manifest.model_dump(mode="json")
                for manifest in repository.list_export_manifests(run_id)
            ]
        )

    return app


def _arm_or_404(arm_id: str) -> ArmId:
    try:
        return ArmId(arm_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"no arm {arm_id}") from exc


def registered_methods(app: FastAPI) -> set[str]:
    """Every HTTP method the app registers. Used by the no-write-routes test."""
    methods: set[str] = set()
    for route in app.routes:
        methods |= set(getattr(route, "methods", set()) or set())
    return methods - {"HEAD", "OPTIONS"}


def route_paths(app: FastAPI) -> Sequence[str]:
    """Every path the app serves, for the route-coverage test."""
    return sorted({str(getattr(route, "path", "")) for route in app.routes})

"""The metrics the pilot is for, and the views derived from its snapshots.

Four primary measurements -- Origin Recall, Identity Drift, Graveyard Echo, and
contradiction analysis -- plus the Graveyard itself and the deterministic secondary
metrics. Everything here reads through the repository protocol and writes evidence
back through it, so the same analysis runs locally and later in a Lambda.
"""

from attention_sink.analysis.contradiction import (
    ContradictionFinding,
    analyse_contradictions,
    classify_answer,
)
from attention_sink.analysis.echo import EchoMeasurement, classify_echo, measure_echo
from attention_sink.analysis.export import (
    EXPORT_FILES,
    EXPORT_LABELS,
    ExportResult,
    ExportStorage,
    LocalExportStorage,
    export_dataset,
    export_labels,
    read_jsonl,
    verify_checksums,
)
from attention_sink.analysis.graveyard import GraveyardEntry, build_graveyard, lineage_of
from attention_sink.analysis.metrics import (
    ECHO_THRESHOLD,
    IDENTITY_QUESTION_IDS,
    METRIC_VERSION,
    ContradictionLabel,
    EchoCategory,
    QuestionScore,
    ScoringMethod,
    SecondaryMetrics,
    cosine_distance,
    identity_document,
    normalize,
    pairwise_distance_matrix,
    recall_averages,
    score_origin_recall,
    secondary_metrics,
)
from attention_sink.analysis.service import AnalysisResult, AnalysisService

__all__ = [
    "ECHO_THRESHOLD",
    "EXPORT_FILES",
    "EXPORT_LABELS",
    "IDENTITY_QUESTION_IDS",
    "METRIC_VERSION",
    "AnalysisResult",
    "AnalysisService",
    "ContradictionFinding",
    "ContradictionLabel",
    "EchoCategory",
    "EchoMeasurement",
    "ExportResult",
    "ExportStorage",
    "GraveyardEntry",
    "LocalExportStorage",
    "QuestionScore",
    "ScoringMethod",
    "SecondaryMetrics",
    "analyse_contradictions",
    "build_graveyard",
    "classify_answer",
    "classify_echo",
    "cosine_distance",
    "export_dataset",
    "export_labels",
    "identity_document",
    "lineage_of",
    "measure_echo",
    "normalize",
    "pairwise_distance_matrix",
    "read_jsonl",
    "recall_averages",
    "score_origin_recall",
    "secondary_metrics",
    "verify_checksums",
]

"""Everything in this system that talks to AWS.

One package, so that "what can reach a cloud service" is answerable by looking at a
directory rather than by grepping for ``boto3``. It holds the DynamoDB repository,
the S3 export storage, the structured logger, the three Lambda handlers, and the
composition root that wires them -- and it is the only place above the model gateway
that imports an AWS SDK.

It sits above the adapter line on purpose. A Lambda handler is a composition root:
it is allowed to know about the domain, the application, the analysis, the read API,
and the store at the same time, because choosing which of each to use is the whole of
its job. ``tests/unit/test_import_boundaries.py`` states that explicitly rather than
letting it happen by accident.

Nothing here is imported by ``packages/pilot``, ``packages/analysis``, or
``packages/api``. The dependency points this way and a test fails if it stops.
"""

from attention_sink.aws.dynamodb import (
    ARM_SNAPSHOT_INDEX,
    DEFAULT_LOCK_TTL_SECONDS,
    RUN_LISTING_INDEX,
    SCHEMA_VERSION,
    DynamoRepository,
    table_definition,
)
from attention_sink.aws.events import CYCLE_COMPLETED_DETAIL_TYPE, CycleCompleted
from attention_sink.aws.exports import S3ExportStorage
from attention_sink.aws.settings import AwsSettings, DeploymentEnvironment
from attention_sink.aws.telemetry import StructuredLogger, log_fields

__all__ = [
    "ARM_SNAPSHOT_INDEX",
    "CYCLE_COMPLETED_DETAIL_TYPE",
    "DEFAULT_LOCK_TTL_SECONDS",
    "RUN_LISTING_INDEX",
    "SCHEMA_VERSION",
    "AwsSettings",
    "CycleCompleted",
    "DeploymentEnvironment",
    "DynamoRepository",
    "S3ExportStorage",
    "StructuredLogger",
    "log_fields",
    "table_definition",
]

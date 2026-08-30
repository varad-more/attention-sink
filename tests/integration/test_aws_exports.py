"""Exports to S3, and the prefix that refuses to be written twice.

The local filesystem path is exercised by the Phase 5 export tests and must keep
working; what is new here is that the same ``export_dataset`` call now writes to a
bucket, and that a canonical prefix is written once.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from attention_sink.analysis import EXPORT_FILES, LocalExportStorage, export_dataset
from attention_sink.aws.exports import CanonicalExportExists, S3ExportStorage
from attention_sink.model_gateway import ModelGateway
from attention_sink.persistence import SqliteRepository
from attention_sink.pilot import ProtocolBundle
from attention_sink.pilot.configuration import RunKind
from attention_sink.pilot.local import build_configuration
from attention_sink.pilot.service import PilotService
from tests.conftest import fixed_clock

BUCKET = "attention-sink-exports-test"
REGION = "us-east-1"
RUN_ID = "run_export"


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def exported(
    tmp_path: Path, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
) -> tuple[PilotService, SqliteRepository]:
    """A one-cycle run in a local store, ready to be exported anywhere."""
    repository = SqliteRepository(tmp_path / "pilot.sqlite3")
    service = PilotService(
        repository=repository,
        bundle=pilot_bundle,
        gateway=pilot_gateway,
        engine_clock=fixed_clock,
    )
    service.create_run(
        run_id=RUN_ID,
        configuration=build_configuration(pilot_bundle, run_id=RUN_ID, gateway=pilot_gateway),
    )
    service.run_next_cycle(RUN_ID)
    return service, repository


def _analysis(
    repository: SqliteRepository, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
) -> Any:
    from attention_sink.analysis import AnalysisService

    return AnalysisService(
        repository=repository, bundle=pilot_bundle, gateway=pilot_gateway
    ).analyse_run(RUN_ID)


def test_an_export_to_s3_writes_every_file_and_its_checksums(
    s3: Any,
    exported: tuple[PilotService, SqliteRepository],
    pilot_bundle: ProtocolBundle,
    pilot_gateway: ModelGateway,
):
    service, repository = exported
    storage = S3ExportStorage(
        bucket=BUCKET, run_id=RUN_ID, export_id="export-1", client=s3, run_kind=RunKind.AWS_STAGING
    )
    result = export_dataset(
        storage,
        run=service.get_run(RUN_ID),
        repository=repository,
        bundle=pilot_bundle,
        analysis=_analysis(repository, pilot_bundle, pilot_gateway),
        export_id="export-1",
    )
    listed = s3.list_objects_v2(Bucket=BUCKET, Prefix=storage.prefix)
    names = {obj["Key"].removeprefix(storage.prefix) for obj in listed["Contents"]}
    assert names == {*EXPORT_FILES, "checksums.sha256"}
    assert result.manifest.directory == f"s3://{BUCKET}/runs/{RUN_ID}/export-1/"


def test_the_checksums_cover_what_was_actually_stored(
    s3: Any,
    exported: tuple[PilotService, SqliteRepository],
    pilot_bundle: ProtocolBundle,
    pilot_gateway: ModelGateway,
):
    """A digest taken after a truncated write would be a digest of a truncation."""
    service, repository = exported
    storage = S3ExportStorage(bucket=BUCKET, run_id=RUN_ID, export_id="export-1", client=s3)
    export_dataset(
        storage,
        run=service.get_run(RUN_ID),
        repository=repository,
        bundle=pilot_bundle,
        analysis=_analysis(repository, pilot_bundle, pilot_gateway),
        export_id="export-1",
    )
    recorded = dict(
        line.split("  ", 1)[::-1]
        for line in storage.read("checksums.sha256").decode("utf-8").splitlines()
        if line.strip()
    )
    for name, digest in recorded.items():
        assert hashlib.sha256(storage.read(name)).hexdigest() == digest


def test_a_canonical_export_is_written_once_and_never_revised(
    s3: Any,
    exported: tuple[PilotService, SqliteRepository],
    pilot_bundle: ProtocolBundle,
    pilot_gateway: ModelGateway,
):
    """No canonical experiment result may be edited, enforced by the store."""
    service, repository = exported
    analysis = _analysis(repository, pilot_bundle, pilot_gateway)
    run = service.get_run(RUN_ID)
    storage = S3ExportStorage(
        bucket=BUCKET,
        run_id=RUN_ID,
        export_id="export-1",
        client=s3,
        run_kind=RunKind.AWS_CANONICAL,
    )
    export_dataset(
        storage,
        run=run,
        repository=repository,
        bundle=pilot_bundle,
        analysis=analysis,
        export_id="export-1",
    )
    assert storage.prefix == f"canonical/{RUN_ID}/"
    with pytest.raises(CanonicalExportExists, match="written once"):
        export_dataset(
            S3ExportStorage(
                bucket=BUCKET,
                run_id=RUN_ID,
                export_id="export-2",
                client=s3,
                run_kind=RunKind.AWS_CANONICAL,
            ),
            run=run,
            repository=repository,
            bundle=pilot_bundle,
            analysis=analysis,
            export_id="export-2",
        )


def test_a_staging_export_may_be_repeated_under_a_new_identifier(
    s3: Any,
    exported: tuple[PilotService, SqliteRepository],
    pilot_bundle: ProtocolBundle,
    pilot_gateway: ModelGateway,
):
    """Re-exporting a non-canonical run is normal: analysis improves."""
    service, repository = exported
    analysis = _analysis(repository, pilot_bundle, pilot_gateway)
    for identifier in ("export-1", "export-2"):
        export_dataset(
            S3ExportStorage(bucket=BUCKET, run_id=RUN_ID, export_id=identifier, client=s3),
            run=service.get_run(RUN_ID),
            repository=repository,
            bundle=pilot_bundle,
            analysis=analysis,
            export_id=identifier,
        )
    assert len(repository.list_export_manifests(RUN_ID)) == 2


def test_objects_carry_a_readable_content_type_and_encryption(
    s3: Any,
    exported: tuple[PilotService, SqliteRepository],
    pilot_bundle: ProtocolBundle,
    pilot_gateway: ModelGateway,
):
    service, repository = exported
    storage = S3ExportStorage(bucket=BUCKET, run_id=RUN_ID, export_id="e", client=s3)
    export_dataset(
        storage,
        run=service.get_run(RUN_ID),
        repository=repository,
        bundle=pilot_bundle,
        analysis=_analysis(repository, pilot_bundle, pilot_gateway),
        export_id="e",
    )
    head = s3.head_object(Bucket=BUCKET, Key=f"{storage.prefix}run-manifest.json")
    assert head["ContentType"] == "application/json"
    assert head["ServerSideEncryption"] == "AES256"
    assert json.loads(storage.read("run-manifest.json"))["run_id"] == RUN_ID


def test_a_download_link_is_time_limited(s3: Any):
    storage = S3ExportStorage(bucket=BUCKET, run_id=RUN_ID, export_id="e", client=s3)
    # The parameter is spelled `Expires` or `X-Amz-Expires` depending on the
    # signature version the client negotiates; both are the same guarantee.
    assert "xpires=" in storage.presigned_url("run-manifest.json")
    for invalid in (30, 7200):
        with pytest.raises(ValueError, match="one minute to one hour"):
            storage.presigned_url("run-manifest.json", expires_in=invalid)


def test_reading_an_object_that_is_not_there_says_so(s3: Any):
    storage = S3ExportStorage(bucket=BUCKET, run_id=RUN_ID, export_id="e", client=s3)
    with pytest.raises(FileNotFoundError):
        storage.read("run-manifest.json")


def test_the_local_storage_replaces_rather_than_merges(tmp_path: Path):
    """The destination holds one export and nothing else.

    A leftover file from an earlier export is another run's record, uncovered by the
    checksum file that ships beside it.
    """
    directory = tmp_path / "dataset"
    storage = LocalExportStorage(directory)
    storage.write({"a.json": b"{}"})
    storage.write({"b.json": b"{}"})
    assert sorted(path.name for path in directory.iterdir()) == ["b.json"]


def test_a_read_that_is_denied_is_not_reported_as_a_missing_file():
    """A denied read is not a missing export.

    Reporting it as one would send a reader looking for a file that is right where it
    should be, while the actual problem is a policy.
    """
    from botocore.exceptions import ClientError

    class _Denied:
        def get_object(self, **_: Any) -> Any:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetObject")

    denied: Any = _Denied()
    storage = S3ExportStorage(bucket=BUCKET, run_id=RUN_ID, export_id="e", client=denied)
    with pytest.raises(ClientError):
        storage.read("run-manifest.json")

"""Exports written to S3, under a prefix that says what may still change.

Satisfies :class:`~attention_sink.analysis.export.ExportStorage`, so an export goes
to a bucket or to a directory through the same call and the analysis package never
learns which.

Two prefixes, and the difference is the whole point of the module.

``runs/{run_id}/{export_id}/`` holds ordinary exports. Re-exporting a run is a normal
thing to do -- analysis improves, a metric is added -- and each one gets its own
export identifier.

``canonical/{run_id}/`` holds the registered experiment's dataset, and this class
refuses to write it twice. The constitution says no canonical result may be manually
edited; an object store where the second write silently wins would make that a matter
of nobody happening to do it. Bucket versioning keeps the history either way, but the
refusal is what makes the guarantee.

Every object is written with a SHA-256 checksum S3 verifies on receipt, so a
truncated upload is a failed request rather than a file whose digest no longer
matches the manifest that ships beside it.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from attention_sink.pilot.configuration import RunKind

if TYPE_CHECKING:  # pragma: no cover - imported for types only
    from mypy_boto3_s3.client import S3Client

__all__ = ["CANONICAL_PREFIX", "RUN_PREFIX", "CanonicalExportExists", "S3ExportStorage"]

RUN_PREFIX = "runs"
"""Where a re-runnable export goes. One directory per export identifier."""

CANONICAL_PREFIX = "canonical"
"""Where the registered experiment's dataset goes, once and never again."""

_CONTENT_TYPES: dict[str, str] = {
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".sha256": "text/plain",
}
"""Content types by extension. Set explicitly because S3 defaults everything to
``binary/octet-stream``, which turns a dataset a reader could inspect in the console
into sixteen downloads."""


class CanonicalExportExists(RuntimeError):
    """A canonical export already exists at this prefix and was not overwritten."""


def _content_type(name: str) -> str:
    suffix = name[name.rfind(".") :] if "." in name else ""
    return _CONTENT_TYPES.get(suffix, "application/octet-stream")


@dataclass(frozen=True, slots=True)
class S3ExportStorage:
    """An export written to one prefix of one private bucket."""

    bucket: str
    run_id: str
    export_id: str
    client: S3Client
    run_kind: RunKind = RunKind.AWS_STAGING

    @property
    def canonical(self) -> bool:
        """Whether this export is the registered experiment's dataset."""
        return self.run_kind.is_canonical

    @property
    def prefix(self) -> str:
        """The key prefix every object of this export is written under."""
        if self.canonical:
            return f"{CANONICAL_PREFIX}/{self.run_id}/"
        return f"{RUN_PREFIX}/{self.run_id}/{self.export_id}/"

    @property
    def location(self) -> str:
        """Where this export went, as it is recorded in the manifest."""
        return f"s3://{self.bucket}/{self.prefix}"

    def write(self, files: Mapping[str, bytes]) -> None:
        """Put every file under this export's prefix.

        Raises:
            CanonicalExportExists: This is a canonical export and the prefix is
                already occupied. Nothing is written.
            ClientError: The bucket refused a write.
        """
        if self.canonical and self._occupied():
            msg = (
                f"a canonical export already exists at {self.location}; "
                f"a registered dataset is written once and is never revised"
            )
            raise CanonicalExportExists(msg)
        for name, data in files.items():
            self._put(name, data)

    def _occupied(self) -> bool:
        """Whether anything already sits under this export's prefix."""
        response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix, MaxKeys=1)
        return response.get("KeyCount", 0) > 0

    def _put(self, name: str, data: bytes) -> None:
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": f"{self.prefix}{name}",
            "Body": data,
            "ContentType": _content_type(name),
            # Verified by S3 on receipt. A truncated upload fails the request rather
            # than landing as an object whose digest no longer matches the checksum
            # file shipped beside it.
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode("ascii"),
            "ServerSideEncryption": "AES256",
            "Metadata": {
                "run-id": self.run_id,
                "export-id": self.export_id,
                "run-kind": self.run_kind.value,
            },
        }
        self.client.put_object(**request)

    def presigned_url(self, name: str, *, expires_in: int = 900) -> str:
        """A time-limited link to one exported object.

        Fifteen minutes by default. The bucket is private and stays private; a link
        that outlived the conversation it was pasted into would be a public bucket
        with extra steps.

        Raises:
            ValueError: ``expires_in`` is outside the range a link may live for.
        """
        if not 60 <= expires_in <= 3600:
            msg = f"a download link may live for one minute to one hour, not {expires_in}s"
            raise ValueError(msg)
        url: str = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": f"{self.prefix}{name}"},
            ExpiresIn=expires_in,
        )
        return url

    def read(self, name: str) -> bytes:
        """Read one exported object back, for verification.

        Raises:
            FileNotFoundError: No such object under this prefix.
            ClientError: Anything else. A denied read or a throttle is not a missing
                file, and reporting it as one would send a reader looking for an
                export that is right where it should be.
        """
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=f"{self.prefix}{name}")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"NoSuchKey", "404"}:
                raise
            msg = f"{self.location}{name}"
            raise FileNotFoundError(msg) from exc
        body: bytes = response["Body"].read()
        return body

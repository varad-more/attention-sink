#!/usr/bin/env python
"""Assemble the Python deployment package the three Lambdas run from.

Built as its own step rather than inside `cdk synth`, for two reasons. Synthesis has
to work on a laptop with no Docker daemon and no network, which rules out both of
CDK's bundling paths. And a deployment package that somebody can list before it goes
anywhere is worth more than one that materialises inside a build tool.

    python scripts/build_lambda_bundle.py            # code and dependencies
    python scripts/build_lambda_bundle.py --no-deps  # code only, for `make synth`

The `--no-deps` bundle is enough to synthesise a template and is refused by the
deploy preflight, which reads the marker this script writes. A bundle that would not
have started is better caught before it is uploaded than after.

`boto3` and `botocore` are deliberately not vendored: the Lambda Python runtime ships
them, and a second copy in the package would shadow the one AWS keeps patched.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE = REPO_ROOT / "dist" / "lambda"
MARKER = "BUNDLE.json"

PACKAGES: tuple[str, ...] = (
    "analysis",
    "api",
    "aws",
    "domain",
    "model_gateway",
    "pilot",
    "policies",
    "protocol",
)
"""Every package the handlers import. ``persistence`` is absent on purpose: a
deployed process must not be able to be pointed at a SQLite file."""

DEPENDENCIES: tuple[str, ...] = (
    "pydantic",
    "pyyaml",
    "fastapi",
    "mangum",
    "strands-agents",
)
"""What the runtime does not already provide. Ordered as declared, not resolved:
``uv`` does the resolving."""

PLATFORM = "aarch64-manylinux2014"
"""Matches the ARM64 architecture the functions are declared with. A wheel built for
the wrong architecture installs cleanly and fails at import, which is the worst place
to find out."""

PYTHON_VERSION = "3.12"

EXCLUDE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store")


def _copy_packages(destination: Path) -> list[str]:
    """Copy every namespace package into the bundle root."""
    copied: list[str] = []
    for name in PACKAGES:
        source = REPO_ROOT / "packages" / name / "attention_sink" / name
        if not source.is_dir():
            msg = f"no package at {source}"
            raise FileNotFoundError(msg)
        shutil.copytree(source, destination / "attention_sink" / name, ignore=EXCLUDE)
        copied.append(name)
    return copied


def _copy_protocol(destination: Path) -> None:
    """Copy the protocol files the handlers load at cold start.

    Shipped inside the package rather than read from S3 at runtime: the protocol is
    what makes a run the experiment it is, and a run whose definition could change
    between two cycles would not be one experiment.
    """
    shutil.copytree(
        REPO_ROOT / "experiment" / "pilot",
        destination / "experiment" / "pilot",
        ignore=EXCLUDE,
    )


def _install_dependencies(destination: Path) -> None:
    """Vendor the third-party wheels, for the Lambda architecture and Python.

    Raises:
        FileNotFoundError: ``uv`` is not on PATH.
        subprocess.CalledProcessError: The install failed. Never swallowed: a bundle
            missing a dependency deploys fine and fails on the first invocation.
    """
    uv = shutil.which("uv")
    if uv is None:
        msg = "uv is not on PATH; the bundle's dependencies are installed with it"
        raise FileNotFoundError(msg)
    subprocess.run(  # noqa: S603 - fixed argument vector, no shell, no user input
        [
            uv,
            "pip",
            "install",
            "--target",
            str(destination),
            "--python-platform",
            PLATFORM,
            "--python-version",
            PYTHON_VERSION,
            # Refuse a source distribution: building one here would produce a wheel
            # for this laptop's architecture and ship it to a machine that is not.
            "--only-binary=:all:",
            *DEPENDENCIES,
        ],
        check=True,
        cwd=REPO_ROOT,
    )


def build(*, with_dependencies: bool = True) -> Path:
    """Build the bundle from scratch and record what it contains.

    Returns:
        The bundle directory.

    Raises:
        FileNotFoundError: A declared package is missing from the checkout.
        subprocess.CalledProcessError: Dependency installation failed.
    """
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    BUNDLE.mkdir(parents=True)

    packages = _copy_packages(BUNDLE)
    _copy_protocol(BUNDLE)
    if with_dependencies:
        _install_dependencies(BUNDLE)

    (BUNDLE / MARKER).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "built_at": datetime.now(UTC).isoformat(),
                "packages": packages,
                "dependencies": "vendored" if with_dependencies else "absent",
                "python_version": PYTHON_VERSION,
                "platform": PLATFORM,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return BUNDLE


def bundle_marker(bundle: Path = BUNDLE) -> dict[str, object] | None:
    """What a built bundle says about itself, or None when there is none."""
    marker = bundle / MARKER
    if not marker.is_file():
        return None
    loaded: dict[str, object] = json.loads(marker.read_text(encoding="utf-8"))
    return loaded


def main(argv: Sequence[str] | None = None) -> int:
    """Build the deployment package.

    Returns:
        A process exit status.
    """
    parser = argparse.ArgumentParser(prog="build_lambda_bundle", description=__doc__)
    parser.add_argument(
        "--no-deps",
        action="store_true",
        help="copy the project's own code only; enough to synthesise, not to deploy",
    )
    arguments = parser.parse_args(argv)
    bundle = build(with_dependencies=not arguments.no_deps)
    marker = bundle_marker(bundle) or {}
    total = sum(path.stat().st_size for path in bundle.rglob("*") if path.is_file())
    print(f"bundle:       {bundle}")
    print(f"packages:     {len(PACKAGES)}")
    print(f"dependencies: {marker.get('dependencies')}")
    print(f"size:         {total / 1_048_576:.1f} MiB")
    if marker.get("dependencies") != "vendored":
        print("NOT DEPLOYABLE: built without dependencies; `make aws-bundle` builds a full one.")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())

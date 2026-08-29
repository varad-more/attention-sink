"""The dependency direction, enforced instead of reviewed.

The pure packages are the part of this system that has to be testable without AWS,
replayable without a network, and reasonable about on paper. That only stays true if
nothing quietly imports an adapter into them, which is exactly the kind of change
that slips past code review.

Purity is declared here rather than inferred, so adding a package is a deliberate
statement about which side of the adapter line it sits on.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

BANNED_TOP_LEVEL = frozenset(
    {
        # AWS and agent SDKs.
        "aws_cdk",
        "aws_lambda_powertools",
        "boto3",
        "botocore",
        "moto",
        "strands",
        # Anything that can open a socket. A pure package that reached the network
        # would make a policy decision depend on something no replay can reproduce,
        # which is the failure this whole boundary exists to prevent.
        "aiohttp",
        "http",
        "httpx",
        "requests",
        "socket",
        "ssl",
        "urllib",
        "urllib3",
        "xmlrpc",
    }
)
"""Modules that must never appear below the adapter line, at any depth."""

PURE_PACKAGES: dict[str, frozenset[str]] = {
    "protocol": frozenset({"attention_sink.protocol"}),
    "domain": frozenset({"attention_sink.domain"}),
    "policies": frozenset({"attention_sink.domain", "attention_sink.policies"}),
}
"""Pure package name to the internal packages it may depend on.

Policies may depend on the domain; the domain and the protocol may depend on nothing
but themselves. Anything absent from a value here is a cycle or an inversion.

Adapter packages are listed separately in :data:`ADAPTER_PACKAGES`: they exist to
import the SDKs the pure packages must not, so only their internal direction is
checked.
"""

ADAPTER_PACKAGES: dict[str, frozenset[str]] = {
    "model_gateway": frozenset({"attention_sink.domain", "attention_sink.model_gateway"}),
}
"""Adapter package name to the internal packages it may depend on.

The model gateway may depend on the domain and never on the policies. An adapter
that knew which mechanism it was serving could come to serve it -- pass the arm to a
prompt, tune a retry for one policy, special-case the summarising arm -- and the
arms would stop differing only in mechanism. It is a short step and nothing else
would catch it, so it is a test.
"""


def _package_root(name: str) -> Path:
    return REPO_ROOT / "packages" / name / "attention_sink" / name


def _present(declared: dict[str, frozenset[str]]) -> list[str]:
    """Declared packages that exist in this checkout, in declaration order.

    Packages arrive with the phase that needs them, so the tables describe intent
    for the whole system while the tests run against what is actually here.
    """
    return [name for name in declared if _package_root(name).is_dir()]


PRESENT = _present(PURE_PACKAGES)
PRESENT_ADAPTERS = _present(ADAPTER_PACKAGES)


def _imported_modules(source: Path) -> set[str]:
    """Return every module name imported by ``source``, in absolute form."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def test_at_least_one_pure_package_is_present():
    assert PRESENT, "no pure package exists; the boundary test would be vacuous"


@pytest.mark.parametrize("name", PRESENT)
def test_package_has_modules_to_check(name: str):
    assert sorted(_package_root(name).rglob("*.py")), f"{name} has no modules"


@pytest.mark.parametrize("name", PRESENT)
def test_no_adapter_imports_below_the_line(name: str):
    offenders: list[str] = []
    for source in sorted(_package_root(name).rglob("*.py")):
        banned = sorted(
            module
            for module in _imported_modules(source)
            if module.split(".")[0] in BANNED_TOP_LEVEL
        )
        offenders.extend(f"{source.relative_to(REPO_ROOT)} imports {m}" for m in banned)

    assert not offenders, "adapter dependencies leaked into a pure package: " + "; ".join(offenders)


@pytest.mark.parametrize("name", PRESENT)
def test_internal_dependencies_point_one_way(name: str):
    allowed = PURE_PACKAGES[name]
    offenders: list[str] = []
    for source in sorted(_package_root(name).rglob("*.py")):
        for module in sorted(_imported_modules(source)):
            if not module.startswith("attention_sink."):
                continue
            if not any(module == p or module.startswith(f"{p}.") for p in allowed):
                offenders.append(f"{source.relative_to(REPO_ROOT)} imports {module}")

    assert not offenders, "dependency direction violated: " + "; ".join(offenders)


def test_at_least_one_adapter_package_is_present():
    assert PRESENT_ADAPTERS, "no adapter package exists; the direction test would be vacuous"


@pytest.mark.parametrize("name", PRESENT_ADAPTERS)
def test_adapters_do_not_import_the_mechanism_they_serve(name: str):
    allowed = ADAPTER_PACKAGES[name]
    offenders: list[str] = []
    for source in sorted(_package_root(name).rglob("*.py")):
        for module in sorted(_imported_modules(source)):
            if not module.startswith("attention_sink."):
                continue
            if not any(module == p or module.startswith(f"{p}.") for p in allowed):
                offenders.append(f"{source.relative_to(REPO_ROOT)} imports {module}")

    assert not offenders, "an adapter reached across the line: " + "; ".join(offenders)

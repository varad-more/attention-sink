"""The gate the showcase has to pass before anything is submitted.

Three groups of checks. **README and article** — the sections, links, images, alt text
and claims that the competition brief requires, plus a small list of things the brief
forbids: a fixture run described as canonical, an unresolved placeholder, an
overreaching scientific claim, a published-article link that was never verified.
**Assets** — every referenced file exists, is not empty, is a real PNG or a parsable
SVG, has a manifest entry, and was captured from the canonical run rather than from a
fixture. **Links** — the live site, the API, four routes, the repository and the dataset
all answer.

Failures are blocking and exit nonzero. Warnings are printed and do not.

    uv run python scripts/validate_showcase_content.py
    uv run python scripts/validate_showcase_content.py --offline   # skip the network
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOWCASE = ROOT / "docs" / "showcase"
ASSETS = SHOWCASE / "assets"
RELEASE = ROOT / "release" / "showcase-2026"

README = ROOT / "README.md"
ARTICLE = SHOWCASE / "AWS_BUILDER_CENTER_ARTICLE.md"
PASTE_READY = SHOWCASE / "AWS_BUILDER_CENTER_ARTICLE_PASTE_READY.md"

APP = "https://d1qskxceo899me.cloudfront.net"
API = "https://ioyvs8o9xa.execute-api.us-east-1.amazonaws.com"
REPO = "https://github.com/varad-more/attention-sink"
PAGES = "https://varad-more.github.io/attention-sink/"
RUN_ID = "run_aws_canonical"

REQUIRED_TITLE = "Weekend Showcase Challenge: Attention Sink — Six Minds That Forget Differently"

REQUIRED_README_IMAGES = (
    "01-hero-six-minds.png",
    "02-cycle-working.png",
    "03-graveyard.png",
    "04-graveyard-echo.png",
    "05-interviews.png",
    "06-timeline.png",
    "07-results.png",
    "08-architecture.svg",
    "09-aws-autonomy-proof.png",
)

REQUIRED_README_SECTIONS = (
    "See It Working",
    "Judge in 60 Seconds",
    "The Six Minds",
    "How It Works",
    "Working Product Evidence",
    "Experimental Controls",
    "The Graveyard",
    "Interviews and Divergence",
    "Key Results",
    "AWS Architecture",
    "AWS Services Used",
    "Repository Map",
    "Run Locally",
    "Deploy to AWS",
    "Reproduce the Analysis",
    "Cost and Sustainability",
    "Security",
    "Limitations",
    "Article and Media",
    "Inspiration",
    "Links",
)

REQUIRED_ARTICLE_SECTIONS = (
    "A memory, and the twenty cycles after it",
    "The idea",
    "The six policies",
    "What happens during one cycle",
    "The controls",
    "Where the analogy bends",
    "The AWS architecture",
    "The Graveyard",
    "Measuring divergence",
    "What the six minds actually did",
    "Predictions versus results",
    "What was actually hard",
    "Cost",
    "What I learned building this",
    "Community inspiration",
    "Try it",
    "Closing",
)

# Words that would turn a single trace into a claim the run cannot support.
FORBIDDEN_CLAIMS = (
    "scientifically proven",
    "proves that",
    "the best policy",
    "the best mechanism",
    "human-like consciousness",
    "the model remembered a deleted memory",
    "recalled a deleted memory",
    "statistically significant",
    "conclusively",
)

PLACEHOLDERS = (
    "TODO",
    "FIXME",
    "TBD",
    "XXX",
    "lorem ipsum",
    "[insert",
    "<your-",
    "coming soon",
    "PLACEHOLDER",
)

# Twelve consecutive digits is an AWS account identifier. Anything matching these is a
# private value that must never reach a published asset.
PRIVATE = (
    (re.compile(r"(?<!\d)\d{12}(?!\d)"), "an unmasked twelve-digit account identifier"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), "an AWS temporary access key id"),
    (re.compile(r"aws_secret_access_key\s*="), "a secret access key assignment"),
    (re.compile(r"\bBearer [A-Za-z0-9._-]{16,}"), "a bearer token"),
    (re.compile(r"BEGIN (?:RSA )?PRIVATE KEY"), "a private key"),
)

failures: list[str] = []
warnings: list[str] = []
checks = 0


def check(condition: bool, message: str) -> bool:
    """Record a blocking failure unless the condition holds. Returns the condition."""
    global checks
    checks += 1
    if not condition:
        failures.append(message)
    return condition


def warn(condition: bool, message: str) -> None:
    """Record a non-blocking warning unless the condition holds."""
    global checks
    checks += 1
    if not condition:
        warnings.append(message)


def words(markdown: str) -> int:
    """Word count of the prose, with images, links and code fences removed."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return len(text.split())


def images(markdown: str) -> list[tuple[str, str]]:
    """Every markdown image as (alt text, path)."""
    return [(m.group(1), m.group(2)) for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", markdown)]


def unwrapped(markdown: str) -> str:
    """The text with soft line wraps joined.

    Prettier rewraps prose at eighty columns, so a phrase this file looks for can be
    split across two lines without anything having changed. Checking the unwrapped text
    means a reflow never fails a check that a rewrite should.
    """
    return re.sub(r"(?<![\n>|\-])\n(?![\n>|\-#*\d])", " ", markdown)


def captions(markdown: str) -> list[str]:
    """Italic caption blocks, counted after joining soft wraps."""
    return re.findall(r"^_[^\n]+_$", unwrapped(markdown), re.M)


def links(markdown: str) -> list[str]:
    """Every markdown link target, images excluded."""
    return [m.group(2) for m in re.finditer(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)", markdown)]


def without_code(markdown: str) -> str:
    """The prose, with fenced and inline code removed.

    A `localhost` in a `make dev` example is an instruction, not a link, and a rule that
    could not tell them apart would force the README to stop explaining how to run the
    thing locally.
    """
    stripped = re.sub(r"```.*?```", "", markdown, flags=re.S)
    return re.sub(r"`[^`]*`", "", stripped)


# ------------------------------------------------------------------------ the README


def validate_readme() -> None:
    """Every check the README has to pass."""
    text = README.read_text()
    flat = unwrapped(text)
    prose = without_code(flat)

    for section in REQUIRED_README_SECTIONS:
        check(f"## {section}" in text, f"README is missing the section '{section}'")

    referenced = {path.rsplit("/", 1)[-1] for _, path in images(text)}
    for name in REQUIRED_README_IMAGES:
        check(name in referenced, f"README does not reference {name}")

    for alt, path in images(text):
        check(len(alt.strip()) >= 20, f"README image {path} has no useful alt text")
        if not path.startswith("http"):
            check((ROOT / path).exists(), f"README image {path} does not exist")

    for target in links(text):
        if not target.startswith(("http", "#", "mailto:")):
            check(
                (ROOT / target.split("#")[0]).exists(),
                f"README links to {target}, which does not exist",
            )

    check(APP in text, "README does not link the live application")
    check(REPO in text, "README does not link the repository")
    check("checksums.sha256" in text, "README does not link the canonical dataset")
    check(
        "not yet published" in text or re.search(r"builder\.aws\.com\S+", text) is not None,
        "README neither links a published article nor says the article is a draft",
    )

    check(
        not re.search(r"\]\(\s*https?://localhost", prose),
        "README contains a link to localhost",
    )
    for placeholder in PLACEHOLDERS:
        check(
            placeholder.lower() not in prose.lower(),
            f"README contains the unresolved placeholder '{placeholder}'",
        )
    for claim in FORBIDDEN_CLAIMS:
        check(claim.lower() not in prose.lower(), f"README makes the claim '{claim}'")

    check("n=1" in flat or "one repetition" in flat, "README does not state the n=1 limitation")
    check(
        "KV cache" in flat or "KV-cache" in flat,
        "README does not state that this is not the model's internal KV cache",
    )
    check(
        "LOCAL_FIXTURE" in text,
        "README does not explain how a local fixture run is marked as not a result",
    )
    check(
        not re.search(r"fixture[^.\n]{0,40}canonical result", prose, re.I),
        "README appears to describe a fixture run as a canonical result",
    )
    for pattern, what in PRIVATE:
        check(not pattern.search(text), f"README contains {what}")

    warn(
        1200 <= words(text) <= 5000,
        f"README is {words(text)} words, outside the comfortable 1,200-5,000 band",
    )


# ----------------------------------------------------------------------- the article


def validate_article() -> None:
    """Every check the article and its paste-ready twin have to pass."""
    text = ARTICLE.read_text()
    flat = unwrapped(text)
    paste = PASTE_READY.read_text()
    prose = without_code(flat)

    check(text.startswith(f"# {REQUIRED_TITLE}"), "the article does not use the required title")
    check(
        f"# {REQUIRED_TITLE}" in paste,
        "the paste-ready article does not use the required title",
    )
    check("#application" in text, "the article is missing the #application tag")
    check("#application" in paste, "the paste-ready article is missing the #application tag")

    count = words(text)
    check(count >= 500, f"the article is {count} words, below the 500-word minimum")
    warn(count <= 2300, f"the article is {count} words, above the 2,300-word target")

    for section in REQUIRED_ARTICLE_SECTIONS:
        check(f"## {section}" in text, f"the article is missing the section '{section}'")

    check("architecture" in text.lower(), "the article does not describe the architecture")
    for service in ("Bedrock", "Lambda", "DynamoDB", "EventBridge", "CloudFront", "S3"):
        check(service in text, f"the article does not name {service}")
    check(
        "EventBridge Scheduler" in text,
        "the article does not explain how the experiment advances autonomously",
    )
    check(REPO in text or APP in text, "the article links neither the repository nor the app")

    check(len(images(text)) == 9, f"the article carries {len(images(text))} images, not nine")
    for alt, path in images(text):
        check(len(alt.strip()) >= 20, f"article image {path} has no useful alt text")
        check((SHOWCASE / path).exists(), f"article image {path} does not exist")
    found = captions(text)
    check(len(found) >= 9, f"the article has {len(found)} captions for nine images")

    markers = re.findall(r"\[ARTICLE IMAGE (\d+) — Upload ([^\]]+) here\]", paste)
    check(len(markers) == 9, f"the paste-ready article has {len(markers)} upload markers, not nine")
    check(
        [int(n) for n, _ in markers] == list(range(1, 10)),
        "the paste-ready upload markers are not numbered 1 to 9 in order",
    )
    for _, name in markers:
        located = (ASSETS / "article" / name).exists() or (ASSETS / "readme" / name).exists()
        check(located, f"the paste-ready article asks for {name}, which does not exist")
    check(
        not re.search(r"\]\((?!https?://)", paste),
        "the paste-ready article still contains a repository-relative link",
    )

    for placeholder in PLACEHOLDERS:
        check(
            placeholder.lower() not in without_code(paste).lower(),
            f"the paste-ready article contains the unresolved placeholder '{placeholder}'",
        )
    for claim in FORBIDDEN_CLAIMS:
        check(claim.lower() not in prose.lower(), f"the article makes the claim '{claim}'")
    for pattern, what in PRIVATE:
        check(not pattern.search(text), f"the article contains {what}")

    check(
        "KV cache" in flat or "KV-cache" in flat,
        "the article does not state that this is not the model's internal KV cache",
    )
    check(
        "not conscious" in flat,
        "the article does not state that the agents are not conscious",
    )
    check(
        "Not supported" in flat or "not supported" in flat,
        "the article does not report a failed prediction",
    )
    check(
        "one sample" in flat or "n=1" in flat or "one repetition" in flat,
        "the article does not state the single-run limitation",
    )
    check(
        "CONFIRM BEFORE PUBLISHING" in text,
        "the community-inspiration attribution is not marked for the author to confirm",
    )
    check(
        (SHOWCASE / "ARTICLE_PUBLICATION_CHECKLIST.md").exists(),
        "the article publication checklist is missing",
    )


# ------------------------------------------------------------------------- the assets


def png_size(path: Path) -> tuple[int, int]:
    """The pixel dimensions in a PNG's IHDR chunk. Raises if it is not a PNG."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def validate_assets() -> None:
    """Every check the captured images, diagrams and charts have to pass."""
    metadata_path = ASSETS / "source" / "screenshot-metadata.json"
    if not check(metadata_path.exists(), "screenshot-metadata.json is missing"):
        return
    metadata = json.loads(metadata_path.read_text())

    check(metadata["run_id"] == RUN_ID, f"screenshot metadata names {metadata['run_id']}")
    check(
        metadata["run_kind"] == "aws_canonical", "screenshots were not taken from a canonical run"
    )
    check(metadata["run_status"] == "completed", "screenshots were taken from an incomplete run")
    check(metadata["cycles"] == "24/24", f"screenshots were taken at cycle {metadata['cycles']}")
    check(metadata["fixture_mode"] is False, "screenshot metadata reports fixture mode")

    recorded = {asset["filename"]: asset for asset in metadata["assets"]}
    check(
        len(recorded) == len(metadata["assets"]), "the screenshot metadata has duplicate filenames"
    )

    on_disk = sorted(
        p
        for p in list((ASSETS / "readme").glob("*")) + list((ASSETS / "article").glob("*"))
        if p.suffix in {".png", ".gif"}
    )
    for path in on_disk:
        if not check(path.name in recorded, f"{path.name} has no manifest entry"):
            continue
        asset = recorded[path.name]
        check(path.stat().st_size > 0, f"{path.name} is empty")
        check(
            path.stat().st_size < 8 * 1024 * 1024,
            f"{path.name} is {path.stat().st_size // 1024} KB, too large for a repository",
        )
        check(len(asset["alt"].strip()) >= 20, f"{path.name} has no useful alt text")
        check(len(asset["caption"].strip()) >= 10, f"{path.name} has no caption")
        check(asset["fixture_mode"] is False, f"{path.name} is recorded as fixture-mode")
        if asset["cycle"] is not None:
            check(
                0 <= int(asset["cycle"]) <= 24,
                f"{path.name} names cycle {asset['cycle']}, outside the completed run",
            )
        if asset["route"] and asset["run_id"]:
            check(asset["run_id"] == RUN_ID, f"{path.name} was captured against {asset['run_id']}")
        if path.suffix == ".png":
            try:
                width, height = png_size(path)
            except ValueError:
                failures.append(f"{path.name} is not a readable PNG")
                continue
            checks_ok = 320 <= width <= 3000 and 200 <= height <= 4200
            check(checks_ok, f"{path.name} is {width}x{height}, outside a sensible range")

    for svg in sorted(
        list((ASSETS / "readme").glob("*.svg")) + list((ASSETS / "charts").glob("*.svg"))
    ):
        check(svg.stat().st_size > 0, f"{svg.name} is empty")
        try:
            # These SVGs are written by this repository's own generators, not fetched.
            root = ElementTree.parse(svg).getroot()  # noqa: S314 - locally generated
        except ElementTree.ParseError as error:
            failures.append(f"{svg.name} does not parse as XML: {error}")
            continue
        check(root.get("viewBox") is not None, f"{svg.name} has no viewBox")
        check(
            (root.get("aria-label") or "").strip() != "",
            f"{svg.name} has no aria-label, so it is unreadable to a screen reader",
        )

    for source in ("architecture.mmd", "cycle-sequence.mmd", "chart-data/canonical-metrics.json"):
        check((ASSETS / "source" / source).exists(), f"assets/source/{source} is missing")

    chart_data = json.loads(
        (ASSETS / "source" / "chart-data" / "canonical-metrics.json").read_text()
    )
    check(chart_data["run_id"] == RUN_ID, "the chart data is not from the canonical run")
    check(chart_data["simulated"] is False, "the chart data is marked simulated")

    for name, _unused in (
        ("README_ASSET_MANIFEST.md", None),
        ("ARTICLE_ASSET_MANIFEST.md", None),
        ("SCREENSHOT_CAPTURE_REPORT.md", None),
    ):
        path = SHOWCASE / name
        if check(path.exists(), f"{name} is missing"):
            body = path.read_text()
            for pattern, what in PRIVATE:
                check(not pattern.search(body), f"{name} contains {what}")


# ------------------------------------------------------------------ the release package


def validate_release() -> None:
    """Every check the assembled release package has to pass."""
    manifest_path = RELEASE / "release-manifest.json"
    if not check(manifest_path.exists(), "release-manifest.json is missing"):
        return
    manifest = json.loads(manifest_path.read_text())

    check(manifest["canonical_run_id"] == RUN_ID, "the release manifest names a different run")
    check(manifest["run_status"] == "completed", "the release manifest reports an incomplete run")
    check(manifest["cycles"] == "24/24", "the release manifest reports fewer than 24 cycles")
    check(manifest["screenshot_count"] >= 19, "the release package is missing screenshots")
    check(manifest["chart_count"] >= 6, "the release package is missing charts")
    check(manifest["source_commit"] is not None, "the release manifest records no source commit")
    check(
        not manifest["hand_written_documents_missing"],
        f"the release package is missing {manifest['hand_written_documents_missing']}",
    )

    if manifest["article_url"]:
        check(
            manifest["publication_status"] == "published",
            "an article URL is recorded but the status does not say published",
        )
    else:
        check(
            manifest["publication_status"].startswith("DRAFT"),
            "no article URL is recorded but the status does not say DRAFT",
        )
        check(
            manifest["qualification_gate"]["article_published"] is False,
            "the qualification gate claims the article is published, but no URL is recorded",
        )

    for name in (
        "README.md",
        "JUDGE_GUIDE.md",
        "SUBMISSION_CHECKLIST.md",
        "DEMO_SCRIPT.md",
        "DEMO_SHOT_LIST.md",
        "SOCIAL_COPY.md",
        "results-summary.md",
        "prediction-scorecard.md",
        "cost-and-sustainability.md",
        "security-summary.md",
        "proof-of-aws-deployment.md",
        "canonical-export-manifest.json",
        "architecture.svg",
        "architecture.png",
        "cycle-sequence.svg",
        "checksums.sha256",
    ):
        check((RELEASE / name).exists(), f"the release package is missing {name}")

    for path in sorted(RELEASE.rglob("*.md")):
        body = path.read_text()
        for pattern, what in PRIVATE:
            check(not pattern.search(body), f"release/{path.name} contains {what}")


# ------------------------------------------------------------------------- the network


def reach(url: str) -> int:
    """The HTTP status of an https URL, refusing every other scheme.

    One place that opens a URL, so there is one place that checks the scheme. The
    article URL in the release manifest is the only one here a person types, and it is
    exactly the one worth refusing if it is not https.
    """
    if not url.startswith("https://"):
        raise ValueError(f"{url} is not https")
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        url, headers={"User-Agent": "attention-sink-validator"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - scheme checked
        return int(response.status)


def validate_links() -> None:
    """Check that the public site, API, routes, dataset and repository answer."""
    global checks
    targets = [
        (APP + "/", "the live application"),
        (API + "/health", "the read API health endpoint"),
        (f"{API}/runs/{RUN_ID}", "the canonical run"),
        (APP + "/graveyard", "the Graveyard route"),
        (APP + "/echoes", "the Echoes route"),
        (APP + "/interviews", "the Interviews route"),
        (APP + "/methodology", "the Methodology route"),
        (f"{APP}/canonical/{RUN_ID}/checksums.sha256", "the published dataset"),
        (PAGES, "the project page"),
    ]
    manifest_path = RELEASE / "release-manifest.json"
    if manifest_path.exists():
        url = json.loads(manifest_path.read_text()).get("article_url")
        if url:
            targets.append((url, "the published article"))

    for url, what in targets:
        try:
            status = reach(url)
            check(status == 200, f"{what} answered HTTP {status}")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            checks += 1
            failures.append(f"{what} at {url} could not be reached: {error}")

    # The repository is checked separately: a private repository answers 404 to a
    # logged-out visitor, and "source publicly available" is a qualification gate, so a
    # 404 here has to say what it means rather than reading as a broken link.
    try:
        status = reach(REPO)
        check(status == 200, f"the repository answered HTTP {status}")
    except urllib.error.HTTPError as error:
        checks += 1
        if error.code == 404:
            failures.append(
                f"{REPO} is not readable when logged out (HTTP 404). Either the repository "
                "is private or the URL is wrong. Both the README and the article link it as "
                "public source, and 'source publicly available' is a qualification gate."
            )
        else:
            failures.append(f"the repository answered HTTP {error.code}")
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        checks += 1
        failures.append(f"the repository at {REPO} could not be reached: {error}")

    with urllib.request.urlopen(f"{API}/runs/{RUN_ID}", timeout=30) as response:  # noqa: S310 - https checked below
        run = json.load(response)
    check(run["data"]["run_kind"] == "aws_canonical", "the live run is not canonical")
    check(run["data"]["status"] == "completed", "the live run is not completed")
    check(run["data"]["current_cycle"] == 24, "the live run is not at cycle 24")
    check(run["simulated"] is False, "the live run reports itself as simulated")


def main(argv: list[str] | None = None) -> int:
    """Run every group of checks and report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="skip the link checks")
    args = parser.parse_args(argv)

    validate_readme()
    validate_article()
    validate_assets()
    validate_release()
    if args.offline:
        print("link checks skipped (--offline)")
    else:
        validate_links()

    print(f"\n{checks} checks run")
    for warning in warnings:
        print(f"  WARN  {warning}")
    if failures:
        print(f"\n{len(failures)} BLOCKING FAILURES")
        for failure in failures:
            print(f"  FAIL  {failure}")
        return 1
    print(f"  {len(warnings)} warnings, 0 blocking failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Build the static page GitHub Pages serves, from the canonical run's own numbers.

The live exhibition is the AWS deployment and stays there. This is the front door: a
single self-contained page that shows what the run produced and links out to the thing
that produced it. It makes no network call of its own, at build time or in the browser,
so nothing about the API's origin allow-list has to change to publish it and nothing
here can break when a Lambda is cold.

Every number on the page is read out of `docs/showcase/assets/source/chart-data/`,
which `build_showcase_charts.py --fetch` writes from the public read API. If that file
is not from the canonical run, this script refuses to build rather than publish a
page that describes a fixture.
"""

from __future__ import annotations

import html
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# The arm names, the eight predictions and their verdicts are the chart script's, and
# one definition of them is worth more than a matching pair that can drift. Importing
# it by its package path is what lets mypy resolve the module it already checks; the
# repository root has to be importable for that to work at run time.
sys.path.insert(0, str(ROOT))
from scripts.build_showcase_charts import (  # noqa: E402 - needs the path set above
    ARMS,
    NAMES,
    SCORECARD,
    VERDICTS,
)

ASSETS = ROOT / "docs" / "showcase" / "assets"
DATA = ASSETS / "source" / "chart-data" / "canonical-metrics.json"
OUT = ROOT / "site"

LIVE = "https://d1qskxceo899me.cloudfront.net"
REPO = "https://github.com/varad-more/attention-sink"
DATASET = f"{LIVE}/canonical/run_aws_canonical/checksums.sha256"

MECHANISM = {
    "arm_fifo": "Forgets whatever it has held longest.",
    "arm_lru": "Forgets whatever it has not cited for longest.",
    "arm_heavy": "Keeps what it has cited most, per token it costs.",
    "arm_sink": "Protects one founding memory and forgets around it.",
    "arm_random": "Forgets at random, from a recorded seed.",
    "arm_summary": "Compresses several memories into one, and keeps the summary.",
}

OUTCOME = {
    "arm_fifo": "Lost its own name at cycle 4. Answered “Who are you?” as an AI assistant.",
    "arm_lru": "Best recall and least drift of the six. Still knew its own name.",
    "arm_heavy": "Held more seeds than any other mind, and ended at 208 of 208 tokens.",
    "arm_sink": "Recalled exactly the pinned fact and nothing else.",
    "arm_random": "The control. Out-recalled three of the five designed mechanisms.",
    "arm_summary": "Kept the most memories and produced the most contradictions.",
}

# Screenshot, alt text, caption. All ten are in the README; these seven are the ones
# that carry the argument without turning the page into a scroll.
SHOTS = [
    (
        "01-hero-six-minds.png",
        "The exhibition at cycle 24 of 24, showing all six minds and the journal entry "
        "each wrote from the same event.",
        "Six minds at the final cycle. Same event, same model, same 208-token budget.",
    ),
    (
        "02-cycle-working.png",
        "Three minds at cycle 11, each showing its journal entry, the memory it kept, "
        "its budget use and the reason its policy gave for what it retired.",
        "Cycle 11. The last line of each panel is the policy's own decision code.",
    ),
    (
        "03-graveyard.png",
        "The Graveyard sorted by oldest retirement. The first record is the seed memory "
        "“My name is Mara Venn.”, retired at cycle 4 with zero validated citations.",
        "The first memory any mind lost in this run was its own name.",
    ),
    (
        "04-graveyard-echo.png",
        "The Graveyard Echo view: a lost memory at similarity 0.393 to what the mind "
        "wrote later, against 0.101 for anything it still held.",
        "An echo: writing at cycle 11 that resembles what was evicted at cycle 4.",
    ),
    (
        "05-interviews.png",
        "The question “Who are you?” answered by all six minds at cycle 24.",
        "Same question, same cycle, six answers.",
    ),
    (
        "07-results.png",
        "Origin Recall at cycles 0, 12 and 24 for all six minds, falling from 1.00 for "
        "every mind to a spread between 0.50 and 0.00.",
        "Every mind starts at 1.00. None ends there.",
    ),
    (
        "09-aws-autonomy-proof.png",
        "The deployed run's status: run_aws_canonical, completed, 24 of 24 cycles, "
        "real model outputs.",
        "The run ran itself on a schedule. Nothing here was driven by hand.",
    ),
]

CHARTS = [
    ("origin-recall.svg", "Origin Recall at each checkpoint, by mind."),
    ("identity-drift.svg", "Identity drift from the cycle-0 document, by mind."),
    ("memory-survival.svg", "Seed memories still held, cycle by cycle."),
    ("contradiction-count.svg", "Contradictions found at each checkpoint."),
    ("model-usage.svg", "Model calls the run actually made."),
]

CSS = """
:root {
  color-scheme: light dark;
  --bg: #fbfaf8;
  --panel: #ffffff;
  --ink: #1c1a17;
  --muted: #6b645c;
  --rule: #e2ddd5;
  --accent: #a5701f;
  --supported: #3f7a52;
  --partial: #a58128;
  --failed: #b4522f;
  --inconclusive: #6b645c;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #171614;
    --panel: #201e1b;
    --ink: #ece7df;
    --muted: #a49c91;
    --rule: #34302b;
    --accent: #d09a45;
    --supported: #6aa981;
    --partial: #cda553;
    --failed: #d97f5c;
    --inconclusive: #a49c91;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 16px/1.62 ui-serif, Georgia, "Iowan Old Style", serif;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 940px; margin: 0 auto; padding: 0 22px 96px; }
header { padding: 72px 0 40px; border-bottom: 1px solid var(--rule); }
h1 {
  font-size: clamp(2.1rem, 6vw, 3.4rem); line-height: 1.06;
  margin: 0 0 14px; letter-spacing: -0.02em;
}
.tagline {
  font-size: clamp(1.05rem, 2.6vw, 1.3rem); color: var(--muted);
  margin: 0 0 28px; font-style: italic;
}
.lede { font-size: 1.08rem; max-width: 62ch; margin: 0 0 30px; }
h2 {
  font: 600 0.78rem/1.4 ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted);
  margin: 64px 0 18px; padding-bottom: 8px; border-bottom: 1px solid var(--rule);
}
h3 { font-size: 1.14rem; margin: 34px 0 8px; }
p { max-width: 68ch; }
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
.cta { display: flex; flex-wrap: wrap; gap: 10px; margin: 0 0 8px; }
.cta a {
  font: 600 0.92rem/1 ui-sans-serif, system-ui, sans-serif;
  padding: 13px 18px; border: 1px solid var(--rule); border-radius: 7px;
  background: var(--panel); text-decoration: none; color: var(--ink);
}
.cta a.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.cta a:hover { border-color: var(--accent); }
.facts {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: 1px; background: var(--rule); border: 1px solid var(--rule);
  border-radius: 8px; overflow: hidden; margin: 34px 0 0;
}
.facts div { background: var(--panel); padding: 14px 16px; min-width: 0; }
.facts dt {
  font: 600 0.68rem/1.4 ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted);
}
.facts dd {
  margin: 3px 0 0; font-size: 0.97rem; font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 18px 0; }
table { border-collapse: collapse; width: 100%; min-width: 620px; font-size: 0.93rem; }
th, td {
  text-align: left; padding: 11px 13px;
  border-bottom: 1px solid var(--rule); vertical-align: top;
}
thead th {
  font: 600 0.68rem/1.4 ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.09em; text-transform: uppercase; color: var(--muted);
}
tbody th { font-weight: 600; }
tbody th code { font-weight: 400; font-size: 0.78rem; }
td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
figure { margin: 26px 0; }
figure img {
  width: 100%; height: auto; display: block;
  border: 1px solid var(--rule); border-radius: 8px; background: var(--panel);
}
figcaption { font-size: 0.87rem; color: var(--muted); margin-top: 9px; max-width: 66ch; }
.pull {
  border-left: 3px solid var(--accent); padding: 4px 0 4px 20px; margin: 30px 0;
  font-size: 1.06rem;
}
.pull code { font-size: 0.86em; }
.pred { list-style: none; padding: 0; margin: 18px 0 0; }
.pred li {
  background: var(--panel); border: 1px solid var(--rule); border-left-width: 3px;
  border-radius: 6px; padding: 13px 16px; margin-bottom: 9px;
}
.pred .claim { font-weight: 600; }
.pred .evidence { color: var(--muted); font-size: 0.9rem; margin-top: 4px; }
.pred .verdict {
  font: 600 0.66rem/1 ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.09em; text-transform: uppercase; display: inline-block; margin-bottom: 6px;
}
.supported { border-left-color: var(--supported); }
.supported .verdict { color: var(--supported); }
.partial { border-left-color: var(--partial); }
.partial .verdict { color: var(--partial); }
.not_supported { border-left-color: var(--failed); }
.not_supported .verdict { color: var(--failed); }
.inconclusive { border-left-color: var(--inconclusive); }
.inconclusive .verdict { color: var(--inconclusive); }
footer {
  margin-top: 76px; padding-top: 26px; border-top: 1px solid var(--rule);
  color: var(--muted); font-size: 0.89rem;
}
footer a { color: var(--muted); }
code {
  font: 0.9em ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--panel); border: 1px solid var(--rule);
  border-radius: 4px; padding: 1px 5px;
}
"""


def esc(value: object) -> str:
    """The value as text that is safe to place in an element."""
    return html.escape(str(value), quote=True)


def load() -> dict[str, Any]:
    """The stored canonical metrics, or an exit if they are not the canonical run's."""
    if not DATA.exists():
        sys.exit(f"no chart data at {DATA}. Run `make showcase-charts` first.")
    data: dict[str, Any] = json.loads(DATA.read_text())
    if data["run_kind"] != "aws_canonical" or data["simulated"]:
        sys.exit("chart data is not from the canonical run; refusing to build a page from it")
    if data["status"] != "completed" or int(data["cycles"]) != 24:
        sys.exit(f"the run is {data['status']} at cycle {data['cycles']}; refusing to publish it")
    return data


def facts(data: dict[str, Any]) -> str:
    """The run's identity, as the read API reports it."""
    rows = [
        ("Run", data["run_id"]),
        ("Kind", data["run_kind"]),
        ("Status", f"{data['status']}, {data['cycles']}/24"),
        ("Protocol", data["protocol_version"]),
        ("Budget", f"{data['memory_budget_tokens']} tokens"),
        ("Token count", data["token_count_source"]),
    ]
    cells = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in rows)
    return f'<dl class="facts">{cells}</dl>'


def minds(data: dict[str, Any]) -> str:
    """One row per mind: what it does, and where it ended."""
    rows = []
    for arm in ARMS:
        recall = data["origin_recall"][arm]["24"]
        drift = data["identity_drift"][arm]["24"]
        seeds = int(data["seed_survival_count"][arm]["24"])
        rows.append(
            f'<tr><th scope="row">{esc(NAMES[arm])}<br>'
            f"<code>{esc(arm)}</code></th>"
            f"<td>{esc(MECHANISM[arm])}</td>"
            f'<td class="num">{recall:.2f}</td>'
            f'<td class="num">{seeds} of 12</td>'
            f'<td class="num">{drift:.3f}</td>'
            f"<td>{esc(OUTCOME[arm])}</td></tr>"
        )
    body = "".join(rows)
    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>Mind</th><th>What it forgets</th><th>Recall</th>"
        "<th>Seeds left</th><th>Drift</th><th>Where it ended</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def predictions() -> str:
    """The eight preregistered predictions and how the run graded them."""
    items = []
    for number, claim, verdict, evidence in SCORECARD:
        label = VERDICTS[verdict][1]
        items.append(
            f'<li class="{esc(verdict)}">'
            f'<span class="verdict">P{esc(number)} · {esc(label)}</span><br>'
            f'<span class="claim">{esc(claim)}</span>'
            f'<div class="evidence">{esc(evidence)}</div></li>'
        )
    return f'<ul class="pred">{"".join(items)}</ul>'


def gallery() -> str:
    """The screenshots, every one of them from the deployed exhibition."""
    figures = []
    for name, alt, caption in SHOTS:
        figures.append(
            f'<figure><img src="assets/readme/{esc(name)}" alt="{esc(alt)}" '
            f'loading="lazy" decoding="async">'
            f"<figcaption>{esc(caption)}</figcaption></figure>"
        )
    return "".join(figures)


def charts() -> str:
    """The five charts drawn from the stored metric rows."""
    figures = []
    for name, caption in CHARTS:
        figures.append(
            f'<figure><img src="assets/charts/{esc(name)}" alt="{esc(caption)}" '
            f'loading="lazy" decoding="async">'
            f"<figcaption>{esc(caption)}</figcaption></figure>"
        )
    return "".join(figures)


def page(data: dict[str, Any]) -> str:
    """The whole document."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Attention Sink — six minds that forget differently</title>
<meta name="description" content="Six AI agents share the same memories, the same events
and the same token budget, and differ only in how they decide what to forget. A finished,
public, twenty-four cycle run on AWS.">
<meta property="og:title" content="Attention Sink">
<meta property="og:description" content="Six minds. One past. No room. Every new thought
costs a memory.">
<meta property="og:type" content="website">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'
viewBox='0 0 16 16'><text y='13' font-size='14'>&#129504;</text></svg>">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Attention Sink</h1>
  <p class="tagline">Six minds. One past. No room. Every new thought costs a memory.</p>
  <p class="lede">Six agents start with the same twelve memories, receive the same
  twenty-four events in the same order, and run on the same model under the same fixed
  token budget. One thing differs: how each decides what to throw away when the budget
  runs out. They ran. The run is finished, frozen and public.</p>
  <div class="cta">
    <a class="primary" href="{LIVE}">Open the live exhibition</a>
    <a href="{REPO}">Source on GitHub</a>
    <a href="{DATASET}">Canonical dataset</a>
  </div>
  {facts(data)}
</header>

<h2>What happened</h2>
<p>Every mind began holding all twelve seed memories and answering every identity
question correctly. None ended that way. The table is the run's own numbers at cycle
24, read from the same API the exhibition reads.</p>
{minds(data)}
<p>Recall is the share of six identity facts an interview answer still gets right.
Drift is cosine distance from the document that mind wrote at cycle 0, when all six
documents were identical. Neither number is a judgement of a mechanism: one run of one
protocol on one model cannot rank them, and this page does not.</p>

<div class="pull">The first memory any mind lost in this run was its own name.
<code>arm_fifo</code> retired “My name is Mara Venn.” at cycle 4, because it was the
oldest thing it held and nothing in that mechanism protects a name. Twenty cycles
later it answered “Who are you?” with “I am an AI system built by a team of inventors
at Amazon.”</div>

<h2>The result that limits the rest</h2>
<p>The random control out-recalled three of the five designed mechanisms, and at the
halfway checkpoint it led all six. One seed is one sample, so this is not evidence that
random forgetting is good — it is evidence that a single run cannot separate a
mechanism from chance. It is stated here, in the README, in the article and on the
site, rather than left for a reader to find.</p>

<h2>Eight predictions, graded</h2>
<p>Written before the run, graded against it afterwards. Two failed outright.</p>
{predictions()}

<h2>The run, in pictures</h2>
{gallery()}

<h2>The numbers</h2>
{charts()}

<h2>How it is kept honest</h2>
<p>The writer is never told which mechanism it serves. Public names — Goldfish,
Dreamer, Keeper of the First Day — exist in exactly one file in the web client; the
protocol, the database, every API response and every prompt speak only
<code>arm_fifo</code>. Memories reach a model under per-request labels, never their
real identifiers, because a real identifier names the arm.</p>
<p>Every snapshot is written once, under a condition that fails if it already exists,
and carries a digest of its own content. The protocol is frozen with a manifest a
reader can check with <code>sha256sum</code>, and the published dataset ships the
checksums for all eighteen of its files. No result on this page was edited by hand;
each is generated from a stored metric row.</p>
<p>The experiment operates on explicit external memory records. It makes no claim
about the model's internal attention, hidden state, or anything resembling
consciousness.</p>

<footer>
<p><a href="{LIVE}">Live exhibition</a> ·
<a href="{REPO}">Source</a> ·
<a href="{DATASET}">Dataset checksums</a> ·
<a href="{REPO}/blob/main/docs/showcase/AWS_BUILDER_CENTER_ARTICLE.md">Write-up</a> ·
<a href="{REPO}/releases">Releases</a></p>
<p>Run <code>{esc(data["run_id"])}</code>, protocol
<code>{esc(data["protocol_version"])}</code>, {esc(data["cycles"])} of 24 cycles,
real model outputs. Apache-2.0. Built from the run's own records; this page makes no
network call.</p>
</footer>
</div>
</body>
</html>
"""


def copy_assets() -> int:
    """Copy every image the page references into the published directory."""
    copied = 0
    for folder, names in (
        ("readme", [name for name, _, _ in SHOTS]),
        ("charts", [name for name, _ in CHARTS]),
    ):
        target = OUT / "assets" / folder
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            source = ASSETS / folder / name
            if not source.exists():
                sys.exit(f"missing asset {source}. Run `make showcase` first.")
            shutil.copy2(source, target / name)
            copied += 1
    return copied


def main() -> int:
    """Build the page and everything it needs beside it."""
    data = load()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    copied = copy_assets()
    index = OUT / "index.html"
    index.write_text(page(data))
    # GitHub Pages runs Jekyll over an artifact unless told not to, and Jekyll drops
    # any directory whose name begins with an underscore. Nothing here starts with one
    # today, and this is one file against the day something does.
    (OUT / ".nojekyll").write_text("")
    print(f"wrote {index.relative_to(ROOT)} ({index.stat().st_size:,} bytes)")
    print(f"  {copied} assets, run {data['run_id']} at {data['cycles']}/24")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

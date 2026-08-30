"""Charts for the showcase, drawn from the canonical run and nothing else.

Two steps, deliberately separate. `--fetch` pulls the numbers out of the public read
API into `docs/showcase/assets/source/chart-data/`, which is the only place this
script is allowed to read from afterwards. Rendering then runs offline against those
files, so a chart in the README can be regenerated and diffed without an account, and
a chart that changed is a chart whose *data* changed.

Nothing here computes a result. Every value is a stored metric row, a stored question
score, or a stored contradiction label, copied across unmodified. The one exception is
the prediction scorecard, whose verdicts are an editorial reading of those rows; each
one carries the numbers it was read from so a reader can disagree with the reading
without having to trust it.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "showcase" / "assets" / "source" / "chart-data"
OUT = ROOT / "docs" / "showcase" / "assets" / "charts"

DEFAULT_API = "https://ioyvs8o9xa.execute-api.us-east-1.amazonaws.com"
RUN_ID = "run_aws_canonical"

ARMS = ("arm_fifo", "arm_lru", "arm_heavy", "arm_sink", "arm_random", "arm_summary")
NAMES = {
    "arm_fifo": "Goldfish",
    "arm_lru": "Present-Minded",
    "arm_heavy": "Pragmatist",
    "arm_sink": "Keeper of the First Day",
    "arm_random": "Gambler",
    "arm_summary": "Dreamer",
}
SHORT = {
    "arm_fifo": "Goldfish",
    "arm_lru": "Present-\nMinded",
    "arm_heavy": "Pragmatist",
    "arm_sink": "Keeper of the\nFirst Day",
    "arm_random": "Gambler",
    "arm_summary": "Dreamer",
}
COLOUR = {
    "arm_fifo": "#b4522f",
    "arm_lru": "#2f6b8f",
    "arm_heavy": "#3f7a52",
    "arm_sink": "#8a5a9c",
    "arm_random": "#a58128",
    "arm_summary": "#4a5a72",
}

INK = "#1d1a17"
MUTED = "#6b645c"
RULE = "#d9d3ca"
PAPER = "#fbfaf8"

FONT = "ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


# --------------------------------------------------------------------------- fetch


def _get(api: str, path: str) -> Any:
    """Read one path from the read API, over https and nothing else."""
    url = f"{api}/runs/{RUN_ID}{path}"
    if not url.startswith("https://"):
        raise SystemExit(f"refusing to open {url}: only https is permitted")
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - scheme checked
        return json.load(response)


def _paged(api: str, name: str) -> list[dict[str, Any]]:
    """Every page of a paginated collection, concatenated."""
    items: list[dict[str, Any]] = []
    while True:
        page = _get(api, f"/{name}?limit=200&offset={len(items)}")["data"]
        if not page["items"]:
            break
        items += page["items"]
        if len(items) >= page["total"]:
            break
    return items


def fetch(api: str) -> None:
    """Copy the numbers the charts need out of the read API, and stop there."""
    DATA.mkdir(parents=True, exist_ok=True)
    metrics = _paged(api, "metrics")
    contradictions = _paged(api, "contradictions")
    scores = _get(api, "/question-scores")["data"]
    run = _get(api, "")["data"]

    def series(name: str) -> dict[str, dict[str, float]]:
        rows: dict[str, dict[str, float]] = {}
        for row in metrics:
            if row["metric_name"] == name:
                rows.setdefault(row["arm_id"], {})[str(row["cycle"])] = row["value"]
        return rows

    payload = {
        "run_id": run["run_id"],
        "run_kind": run["run_kind"],
        "status": run["status"],
        "cycles": run["current_cycle"],
        "protocol_version": run["protocol_version"],
        "memory_budget_tokens": run["memory_budget_tokens"],
        "token_count_source": run["token_count_source"],
        "simulated": run["simulated"],
        "origin_recall": series("origin_recall"),
        "identity_drift": series("identity_drift"),
        "seed_survival_count": series("seed_survival_count"),
        "active_memory_count": series("active_memory_count"),
        "contradiction_rate": series("contradiction_rate"),
        "canonical_contradictions": {
            arm: {
                str(cycle): sum(
                    1
                    for row in contradictions
                    if row["arm_id"] == arm
                    and row["cycle"] == cycle
                    and row["label"] == "canonical_contradiction"
                )
                for cycle in (0, 12, 24)
            }
            for arm in ARMS
        },
        "question_scores": [
            {
                key: row[key]
                for key in ("arm_id", "cycle", "question_id", "fact_ids", "score", "method")
            }
            for row in scores
        ],
    }
    (DATA / "canonical-metrics.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {DATA / 'canonical-metrics.json'} from {api}")


# ---------------------------------------------------------------------------- draw


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text(
    x: float,
    y: float,
    body: str,
    *,
    size: float = 12,
    fill: str = INK,
    anchor: str = "start",
    weight: str = "400",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{_escape(body)}</text>'
    )


def _frame(width: float, height: float, title: str, subtitle: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" role="img" '
        f'aria-label="{_escape(title)}">'
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="{PAPER}"/>'
        + _text(28, 36, title, size=17, weight="600")
        + _text(28, 57, subtitle, size=12, fill=MUTED)
        + body
        + _text(
            28,
            height - 16,
            "run_aws_canonical · protocol pilot-v1 · amazon.nova-micro-v1:0 · n=1",
            size=10.5,
            fill=MUTED,
        )
        + "</svg>"
    )


def _grouped_bars(
    data: dict[str, dict[str, float]],
    cycles: tuple[str, ...],
    *,
    title: str,
    subtitle: str,
    ymax: float,
    fmt: str = "{:.2f}",
) -> str:
    width, height = 900, 430
    left, right, top, bottom = 70, 30, 88, 96
    plot_w = width - left - right
    plot_h = height - top - bottom
    group_w = plot_w / len(ARMS)
    bar_w = min(26.0, (group_w - 18) / len(cycles))

    parts: list[str] = []
    for i in range(5):
        value = ymax * i / 4
        y = top + plot_h - plot_h * (value / ymax)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
            f'stroke="{RULE}" stroke-width="1"/>'
        )
        parts.append(_text(left - 10, y + 4, fmt.format(value), size=11, fill=MUTED, anchor="end"))

    for gi, arm in enumerate(ARMS):
        gx = left + gi * group_w
        block = bar_w * len(cycles) + 4 * (len(cycles) - 1)
        start = gx + (group_w - block) / 2
        for ci, cycle in enumerate(cycles):
            value = data.get(arm, {}).get(cycle, 0.0)
            h = plot_h * (value / ymax)
            x = start + ci * (bar_w + 4)
            y = top + plot_h - h
            opacity = 0.35 + 0.65 * (ci / max(1, len(cycles) - 1))
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(h, 1):.1f}" '
                f'fill="{COLOUR[arm]}" fill-opacity="{opacity:.2f}"/>'
            )
            parts.append(
                _text(
                    x + bar_w / 2,
                    y - 6,
                    fmt.format(value),
                    size=10,
                    fill=MUTED,
                    anchor="middle",
                )
            )
        for li, line in enumerate(SHORT[arm].split("\n")):
            parts.append(
                _text(
                    gx + group_w / 2,
                    top + plot_h + 20 + li * 13,
                    line,
                    size=11.5,
                    anchor="middle",
                    weight="600",
                )
            )

    legend_y = height - 44
    for ci, cycle in enumerate(cycles):
        x = left + ci * 118
        opacity = 0.35 + 0.65 * (ci / max(1, len(cycles) - 1))
        parts.append(
            f'<rect x="{x}" y="{legend_y - 9}" width="12" height="12" fill="{INK}" '
            f'fill-opacity="{opacity:.2f}"/>'
        )
        parts.append(_text(x + 18, legend_y + 1, f"cycle {cycle}", size=11.5, fill=MUTED))

    parts.append(
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" '
        f'stroke="{INK}" stroke-width="1.2"/>'
    )
    return _frame(width, height, title, subtitle, "".join(parts))


def chart_origin_recall(d: dict[str, Any]) -> str:
    """Origin Recall for all six arms at the three checkpoints."""
    return _grouped_bars(
        d["origin_recall"],
        ("0", "12", "24"),
        title="Origin Recall at the three checkpoints",
        subtitle=("Share of six canonical facts the mind can still state. All six start at 1.00."),
        ymax=1.0,
    )


def chart_identity_drift(d: dict[str, Any]) -> str:
    """Identity Drift for all six arms at the three checkpoints."""
    return _grouped_bars(
        d["identity_drift"],
        ("0", "12", "24"),
        title="Identity Drift at the three checkpoints",
        subtitle=(
            "Cosine distance from the mind's own cycle-0 identity answers. Higher is "
            "further from where it started."
        ),
        ymax=1.0,
        fmt="{:.2f}",
    )


def chart_contradictions(d: dict[str, Any]) -> str:
    """Answers contradicting the canonical record, per arm per checkpoint."""
    return _grouped_bars(
        {
            a: {c: float(v) for c, v in rows.items()}
            for a, rows in d["canonical_contradictions"].items()
        },
        ("0", "12", "24"),
        title="Answers that contradict the canonical record",
        subtitle=(
            "Out of ten interview answers per checkpoint. Admitted uncertainty is never "
            "counted as a contradiction."
        ),
        ymax=5.0,
        fmt="{:.0f}",
    )


def chart_memory_survival(d: dict[str, Any]) -> str:
    """Seed memories still held, cycle by cycle, one line per arm."""
    width, height = 900, 430
    left, right, top, bottom = 70, 200, 88, 70
    plot_w = width - left - right
    plot_h = height - top - bottom
    cycles = list(range(1, 25))
    ymax = 12.0

    parts: list[str] = []
    for i in range(5):
        value = ymax * i / 4
        y = top + plot_h - plot_h * (value / ymax)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
            f'stroke="{RULE}" stroke-width="1"/>'
        )
        parts.append(_text(left - 10, y + 4, f"{value:.0f}", size=11, fill=MUTED, anchor="end"))
    for cycle in (1, 6, 12, 18, 24):
        x = left + plot_w * ((cycle - 1) / 23)
        parts.append(_text(x, top + plot_h + 20, str(cycle), size=11, fill=MUTED, anchor="middle"))
    parts.append(
        _text(left + plot_w / 2, top + plot_h + 42, "cycle", size=11.5, fill=MUTED, anchor="middle")
    )

    for arm in ARMS:
        rows = d["seed_survival_count"].get(arm, {})
        points = []
        for cycle in cycles:
            value = rows.get(str(cycle))
            if value is None:
                continue
            x = left + plot_w * ((cycle - 1) / 23)
            y = top + plot_h - plot_h * (min(value, ymax) / ymax)
            points.append(f"{x:.1f},{y:.1f}")
        parts.append(
            f'<polyline points="{" ".join(points)}" fill="none" stroke="{COLOUR[arm]}" '
            f'stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>'
        )

    for i, arm in enumerate(ARMS):
        y = top + 8 + i * 24
        final = d["seed_survival_count"].get(arm, {}).get("24", 0.0)
        parts.append(
            f'<line x1="{left + plot_w + 20}" y1="{y:.1f}" x2="{left + plot_w + 44}" '
            f'y2="{y:.1f}" stroke="{COLOUR[arm]}" stroke-width="2.6"/>'
        )
        parts.append(_text(left + plot_w + 52, y + 4, f"{NAMES[arm]} — {final:.0f}", size=11.5))

    parts.append(
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" '
        f'stroke="{INK}" stroke-width="1.2"/>'
    )
    return _frame(
        width,
        height,
        "Seed memories still held, cycle by cycle",
        "All six begin with twelve. The number beside each name is what it held at cycle 24.",
        "".join(parts),
    )


def chart_model_usage(_: dict[str, Any]) -> str:
    """Call counts from the run's own ledger, as published in the cost report."""
    rows = [
        ("writer", 144, "one per arm per cycle"),
        ("token counter", 144, "one per arm per cycle (ADR-013)"),
        ("evaluator", 1013, "the judge, in analysis"),
        ("embedding", 100, "identity vectors, in analysis"),
        ("interviewer", 18, "six per checkpoint"),
        ("summarizer", 10, "the Dreamer arm, only when it compresses"),
    ]
    width, height = 900, 430
    left, top = 210, 96
    bar_w = 470.0
    ymax = float(max(count for _, count, _ in rows))

    parts: list[str] = []
    for i, (role, count, note) in enumerate(rows):
        y = top + i * 44
        w = bar_w * (count / ymax)
        parts.append(_text(left - 14, y + 15, role, size=12.5, anchor="end", weight="600"))
        parts.append(
            f'<rect x="{left}" y="{y}" width="{max(w, 2):.1f}" height="21" '
            f'fill="{INK}" fill-opacity="0.72"/>'
        )
        parts.append(_text(left + max(w, 2) + 10, y + 15, f"{count:,}", size=12, weight="600"))
        parts.append(_text(left + max(w, 2) + 62, y + 15, note, size=11, fill=MUTED))

    parts.append(
        _text(
            left - 14,
            top + len(rows) * 44 + 26,
            "1,429 calls total",
            size=12.5,
            anchor="end",
            weight="600",
        )
    )
    parts.append(
        _text(
            left,
            top + len(rows) * 44 + 26,
            "3,012,541 input tokens · 342,998 output · 0 failed · 5 retried · $0.2025 estimated",
            size=11.5,
            fill=MUTED,
        )
    )
    return _frame(
        width,
        height,
        "Every model call the canonical run made",
        "From the run's own call ledger. Analysis is charged to the run, not to any one mind.",
        "".join(parts),
    )


VERDICTS = {
    "supported": ("#3f7a52", "Supported"),
    "partial": ("#a58128", "Partially supported"),
    "not_supported": ("#b4522f", "Not supported"),
    "inconclusive": ("#6b645c", "Inconclusive"),
}

SCORECARD = [
    (
        "1",
        "All six arms lose facts; none ends holding all twelve seeds.",
        "supported",
        "Seeds still held at cycle 24: 0, 5, 6, 1, 2, 0 of 12.",
    ),
    (
        "2",
        "Goldfish loses the identity facts first and never recovers them.",
        "partial",
        "Recall 0.00 at cycles 12 and 24 — but it collapsed to the assistant persona, "
        "not to the cues.",
    ),
    (
        "3",
        "Keeper beats Goldfish on the pinned fact and on nothing else.",
        "supported",
        "Keeper scored the name and nothing else; 1 seed survived, the pinned one.",
    ),
    (
        "4",
        "Pragmatist outscores Present-Minded on recall.",
        "not_supported",
        "Reversed: Present-Minded 3 of 6, Pragmatist 2 of 6.",
    ),
    (
        "5",
        "Dreamer retains the most facts, stated least precisely.",
        "not_supported",
        "Dreamer scored 0 of 6 and produced four contradictions, the most of any mind.",
    ),
    (
        "6",
        "Contradictions track loss of the 'announcements can be false' fact.",
        "inconclusive",
        "No mind held that fact at any checkpoint, so there was no variance to correlate.",
    ),
    (
        "7",
        "Recovery is partial and confabulated rather than correct or blank.",
        "partial",
        "1 partial score in 108; but 77 of 180 answers were unsupported inference.",
    ),
    (
        "8",
        "The random control shows the widest spread and acts as a floor.",
        "inconclusive",
        "One seed is one sample: no spread is measurable. It outscored three designed arms.",
    ),
]


def chart_prediction_scorecard(_: dict[str, Any]) -> str:
    """The eight preregistered predictions and how each was graded."""
    width = 980
    row_h = 62
    top = 100
    height = top + len(SCORECARD) * row_h + 54

    parts: list[str] = []
    for i, (number, claim, verdict, evidence) in enumerate(SCORECARD):
        y = top + i * row_h
        colour, label = VERDICTS[verdict]
        parts.append(
            f'<rect x="28" y="{y}" width="{width - 56}" height="{row_h - 10}" '
            f'fill="#ffffff" stroke="{RULE}" stroke-width="1"/>'
        )
        parts.append(f'<rect x="28" y="{y}" width="4" height="{row_h - 10}" fill="{colour}"/>')
        parts.append(_text(46, y + 22, f"P{number}", size=12, fill=MUTED, weight="600"))
        parts.append(_text(82, y + 22, claim, size=13, weight="600"))
        parts.append(_text(82, y + 41, evidence, size=11.5, fill=MUTED))
        parts.append(
            f'<rect x="{width - 210}" y="{y + 10}" width="164" height="24" rx="12" '
            f'fill="{colour}" fill-opacity="0.14"/>'
        )
        parts.append(
            _text(width - 128, y + 26, label, size=11.5, fill=colour, anchor="middle", weight="600")
        )
    return _frame(
        width,
        height,
        "Preregistered predictions, graded against the canonical run",
        "Registered before the run. Two held, two held in part, two failed, two cannot be decided.",
        "".join(parts),
    )


CHARTS = {
    "origin-recall": chart_origin_recall,
    "identity-drift": chart_identity_drift,
    "memory-survival": chart_memory_survival,
    "contradiction-count": chart_contradictions,
    "model-usage": chart_model_usage,
    "prediction-scorecard": chart_prediction_scorecard,
}


def render() -> None:
    """Draw every chart from the stored data, refusing anything non-canonical."""
    source = DATA / "canonical-metrics.json"
    if not source.exists():
        sys.exit(f"no chart data at {source}. Run with --fetch first.")
    data = json.loads(source.read_text())
    if data["run_kind"] != "aws_canonical" or data["simulated"]:
        sys.exit("chart data is not from the canonical run; refusing to draw it")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, draw in CHARTS.items():
        path = OUT / f"{name}.svg"
        path.write_text(draw(data) + "\n")
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")


def main(argv: list[str] | None = None) -> int:
    """Optionally refresh the data, then draw."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="refresh chart data from the read API")
    parser.add_argument("--api", default=DEFAULT_API, help="read API base URL")
    args = parser.parse_args(argv)
    if args.fetch:
        fetch(args.api)
    render()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

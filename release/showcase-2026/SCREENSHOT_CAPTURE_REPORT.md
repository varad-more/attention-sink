# Screenshot capture report

Written by `scripts/capture_showcase_screenshots.mjs` on its last successful run.

## What was captured, and from where

|                |                                                        |
| -------------- | ------------------------------------------------------ |
| Exhibition     | https://d1qskxceo899me.cloudfront.net                  |
| Read API       | https://ioyvs8o9xa.execute-api.us-east-1.amazonaws.com |
| Run            | `run_aws_canonical` — aws_canonical, completed         |
| Cycles         | 24 of 24                                               |
| Protocol       | pilot-v1, 208 tokens, `bedrock_converse_usage`         |
| Assets written | 21                                                     |
| Captured at    | 2026-08-30T23:04:10Z                                   |

## What the capture checked before it took anything

Four checks, all of which a non-production site fails:

1. The read API reports `run_kind` `aws_canonical`, not a local or staging kind.
2. The run reports status `completed` at cycle 24 of 24.
3. No page under capture renders the `simulated-banner` element.
4. Every page's own footer states that its words came from real model outputs.

Two further checks are made per shot rather than up front: the Graveyard's oldest
entry must be the seed name memory, and the interview view must return exactly six
answers. A change to either fails the capture rather than producing a wrong caption.

## What was refused

- A local build: it renders the LOCAL SIMULATION banner, which the capture refuses.
- A fixture run: the footer says simulated model outputs, which the capture refuses.
- Storybook or component fixtures: none exist in this repository.
- A staging deployment: it holds three cycles and no results.
- Hand-edited JSON: every figure is read live from the read API at capture time.

## The assets

| File                                  | Pixels      | Size   | Source                                                                                                                                                                                                       |
| ------------------------------------- | ----------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `01-hero-six-minds.png`               | 1440 x 1900 | 469 KB | `/cycle/24`                                                                                                                                                                                                  |
| `02-cycle-working.png`                | 1264 x 1242 | 265 KB | `/cycle/11`                                                                                                                                                                                                  |
| `03-graveyard.png`                    | 1264 x 862  | 112 KB | `/graveyard?sort=oldest`                                                                                                                                                                                     |
| `04-graveyard-echo.png`               | 1264 x 780  | 92 KB  | `/echoes?category=partial_reconstruction`                                                                                                                                                                    |
| `05-interviews.png`                   | 1264 x 2128 | 235 KB | `/interviews?cycle=24&question=q01`                                                                                                                                                                          |
| `06-timeline.png`                     | 1264 x 901  | 96 KB  | `/timeline`                                                                                                                                                                                                  |
| `07-results.png`                      | 2400 x 1146 | 97 KB  | charts/origin-recall.svg                                                                                                                                                                                     |
| `08-architecture.png`                 | 2480 x 1972 | 390 KB | readme/08-architecture.svg                                                                                                                                                                                   |
| `09-aws-autonomy-proof.png`           | 2560 x 1640 | 407 KB | source/deployment-evidence.html                                                                                                                                                                              |
| `10-mobile.png`                       | 780 x 2480  | 224 KB | `/graveyard?sort=oldest`                                                                                                                                                                                     |
| `article-01-opening-graveyard.png`    | 1184 x 836  | 109 KB | `/memory/mem_arm_fifo_000000`                                                                                                                                                                                |
| `article-02-six-minds.png`            | 1184 x 1387 | 316 KB | `/cycle/24`                                                                                                                                                                                                  |
| `article-03-cycle-flow.png`           | 2400 x 1568 | 178 KB | readme/cycle-sequence.svg                                                                                                                                                                                    |
| `article-04-architecture.png`         | 2400 x 1908 | 393 KB | readme/08-architecture.svg                                                                                                                                                                                   |
| `article-05-graveyard-echo.png`       | 1184 x 776  | 89 KB  | `/echoes?category=partial_reconstruction`                                                                                                                                                                    |
| `article-06-interviews.png`           | 1184 x 2106 | 236 KB | `/interviews?cycle=24&question=q03`                                                                                                                                                                          |
| `article-07-results-chart.png`        | 2400 x 1146 | 97 KB  | charts/origin-recall.svg                                                                                                                                                                                     |
| `article-08-prediction-scorecard.png` | 2400 x 1592 | 297 KB | charts/prediction-scorecard.svg                                                                                                                                                                              |
| `article-09-mobile.png`               | 780 x 1688  | 173 KB | `/cycle/24`                                                                                                                                                                                                  |
| `cycle-sequence.png`                  | 2480 x 1620 | 182 KB | readme/cycle-sequence.svg                                                                                                                                                                                    |
| `optional-demo.gif`                   | 900 x 570   | 576 KB | `/cycle/24 → /cycle/24 → /graveyard?sort=oldest → /memory/mem_arm_fifo_000000 → /echoes?category=partial_reconstruction → /interviews?cycle=24&question=q01 → /interviews?cycle=24&question=q01 → /timeline` |

## Rerunning it

```bash
make showcase            # all of the below, in order

make showcase-charts     # refresh the numbers and redraw the diagrams
make showcase-evidence   # re-collect the AWS evidence card
make showcase-capture    # re-take every image from the deployed site
make showcase-release    # rebuild the paste-ready article and the package
make showcase-verify     # check the result
```

The capture needs no AWS credential — it reads the public site and the public API.
Only `build_deployment_evidence.py` uses the default credential chain, and it reads
three describe-style calls and writes nothing.

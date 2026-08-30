# Demo shot list

The eight shots in `optional-demo.gif`, and how to reproduce them. The GIF is captured by
`scripts/capture_showcase_screenshots.mjs` at two frames per second, 900 pixels wide,
about nineteen seconds.

| # | Route | Hold | What has to be visible |
| --- | --- | --- | --- |
| 1 | `/cycle/24` | 5 frames | Title, tagline, "24 of 24", "completed", the 208-token budget |
| 2 | `/cycle/24`, scrolled 900px | 4 frames | Two mind panels side by side with different journal text and different policy reasons |
| 3 | `/graveyard?sort=oldest` | 5 frames | "157 of 157 memories", and `mem_arm_fifo_000000` as the first record |
| 4 | `/memory/mem_arm_fifo_000000` | 5 frames | The memory text, born cycle 0, retired cycle 4, `evicted_oldest`, the snapshot digest |
| 5 | `/echoes?category=partial_reconstruction` | 5 frames | Goldfish cycle 11, both similarities, the 0.292 delta, and the caveat sentence above the list |
| 6 | `/interviews?cycle=24&question=q01` | 5 frames | The read-only notice, the question, the first answers |
| 7 | `/interviews?cycle=24&question=q01`, scrolled 700px | 4 frames | Enough answers visible to see three names and one non-answer |
| 8 | `/timeline` | 5 frames | Six tracks, the checkpoint rules, the cycle-24 table |

## Rules the capture enforces

- No cursor movement and no scroll animation. Frames are taken from settled pages.
- No loading skeletons: every shot waits for `networkidle` plus a settle delay.
- No fixture data: the capture aborts if any page renders the simulation banner or if the run is not `aws_canonical` at 24 of 24.
- No private identifiers on screen.

## Rebuilding it

```bash
node scripts/capture_showcase_screenshots.mjs
```

Needs `ffmpeg` on the path. Without it the script writes
`screenshots/demo-walkthrough.png` as a documented still fallback instead of producing a
broken GIF, and records the substitution in `README_ASSET_MANIFEST.md`.

## A longer video

There is none. If one is made, it goes in the README's **Article and Media** section and
in `release-manifest.json`, and only after the link has been opened and checked.

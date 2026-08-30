# Article publication checklist

Builder Center takes the text in one box and the images one at a time, so publishing is
a sequence rather than a paste. Work down this list in order.

The article to paste is
[`AWS_BUILDER_CENTER_ARTICLE_PASTE_READY.md`](AWS_BUILDER_CENTER_ARTICLE_PASTE_READY.md).
It is generated from
[`AWS_BUILDER_CENTER_ARTICLE.md`](AWS_BUILDER_CENTER_ARTICLE.md) by
`scripts/build_paste_ready_article.py`; if something needs changing, change the source
and regenerate, or the two will disagree.

## Before you open the browser

1. Read the "What I learned building this" section. It is written only from what this repository records, because that is the only source there was. If you want it to reach further back, those sentences are yours to write.
2. Decide on the community-inspiration section, which carries a `[CONFIRM BEFORE PUBLISHING]` marker because it names a real person. Keep it, rewrite it, or delete the section — all three are fine, and the article reads without it.
3. Run `uv run python scripts/validate_showcase_content.py`. It checks the article's title, tag, sections, word count, image markers, links and claims. Fix anything it reports.

## Publishing

4. Open the paste-ready article and copy everything below the HTML comment.
5. Confirm the title is exactly: **Weekend Showcase Challenge: Attention Sink — Six Minds That Forget Differently**
6. Add the tag `#application`.
7. Upload the nine images in the order below, replacing each `[ARTICLE IMAGE n]` block with the image. Set the caption and the alt text from the table — the alt text is not optional, it is the only version of the image some readers get.
8. Verify the live application link opens: https://d1qskxceo899me.cloudfront.net
9. Verify the repository link opens: https://github.com/varad-more/attention-sink
10. Verify the dataset link downloads: https://d1qskxceo899me.cloudfront.net/canonical/run_aws_canonical/checksums.sha256
11. Confirm the inspired-builder section says what you decided at step 2, or is gone.
12. Confirm the "What I learned building this" section is in your voice.
13. Preview on desktop. Check that no image is scaled down so far that its numbers are unreadable.
14. Preview on mobile. Check the same thing.
15. Confirm the word count is in the 1,500–2,300 band Builder Center expects.
16. Publish, inside the competition window.

## After publishing

17. Open the published article in a private window, logged out, and read it end to end.
18. Record the article URL.
19. Put that URL in the README, replacing the "not yet published" line in **Article and Media** and the **Builder Center article** row in the header table.
20. Put the same URL in `release/showcase-2026/release-manifest.json` under `article_url`, and change `publication_status` to `published`.
21. Re-run `uv run python scripts/validate_showcase_content.py`. With a URL present it checks that the URL actually resolves.
22. Commit the publication update.

## The nine images, in upload order

<!-- ASSET TABLE -->

| Order | Filename                              | Caption                                                                                                                               | Alt text                                                                                                                                                                                                                                     | Uploaded |
| ----- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| 1     | `article-01-opening-graveyard.png`    | A memory in the public Graveyard after it was removed from the agent's active context.                                                | The public record for memory mem_arm_fifo_000000, "My name is Mara Venn.", a seed memory born at cycle 0 and retired at cycle 4 by the fifo-v1 policy with zero validated citations, alongside the snapshot digest that proves the decision. | [ ]      |
| 2     | `article-02-six-minds.png`            | Two of the six at the last cycle. The panels are identical in structure because everything except the forgetting rule is identical.   | Two of the six minds at cycle 24 side by side, each with its journal entry, the memory it kept, its origin recall and identity drift, and the reason its policy gave.                                                                        | [ ]      |
| 3     | `article-03-cycle-flow.png`           | One cycle. Six arms are written in a single transaction, or none of them are.                                                         | The thirteen steps of one cycle, from the scheduler to the public read API.                                                                                                                                                                  | [ ]      |
| 4     | `article-04-architecture.png`         | Eleven AWS services. The account identifier is masked and no ARN is complete.                                                         | The deployed AWS architecture for Attention Sink in us-east-1, showing the scheduled generation path, persistence, asynchronous analysis, the public read path, the dataset export and the cross-cutting monitoring.                         | [ ]      |
| 5     | `article-05-graveyard-echo.png`       | The strongest partial reconstruction in the run. The page states plainly that this is a measured distance and not evidence of access. | Goldfish at cycle 11: the forgotten memory "Every clock in the station shows 03:17." beside the memory it wrote afterwards, with forgotten similarity 0.393, active similarity 0.101 and an echo delta of 0.292 against a 0.080 threshold.   | [ ]      |
| 6     | `article-06-interviews.png`           | "Who is Ivo?" at cycle 24. At cycle 0 all six gave the same answer.                                                                   | The same question, "Who is Ivo?", answered by all six minds at cycle 24, each answer scored for factual recall against the canonical record with its cited memories and contradiction status.                                                | [ ]      |
| 7     | `article-07-results-chart.png`        | What survived. Present-Minded finished highest at 0.50; two minds finished at zero.                                                   | Origin Recall at cycles 0, 12 and 24 for all six minds, falling from 1.00 for every mind to between 0.50 and 0.00.                                                                                                                           | [ ]      |
| 8     | `article-08-prediction-scorecard.png` | Predictions registered before the run, graded after it. Two failed outright.                                                          | The eight preregistered predictions graded against the canonical run: two supported, two partially supported, two not supported and two inconclusive.                                                                                        | [ ]      |
| 9     | `article-09-mobile.png`               | The exhibition on a phone. Same data, same run, no separate mobile build.                                                             | The exhibition on a phone at cycle 24, showing the title, the tagline "Six minds. One past. No room.", the run status bar and the first mind.                                                                                                | [ ]      |

<!-- /ASSET TABLE -->

**Uploaded** is unticked on purpose. Nothing in this repository can observe a Builder
Center upload, so nothing in this repository should claim one happened. Tick each box
by hand as you go.

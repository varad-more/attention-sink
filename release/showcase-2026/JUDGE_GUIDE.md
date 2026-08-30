# Judge guide

Attention Sink is a controlled experiment in application-managed AI memory, running on
AWS, with a completed canonical run and a published dataset. Six agents began identical
and differ in exactly one thing: what each one throws away when its memory budget runs
out.

Everything below is checkable without running anything.

## Sixty seconds

1. **https://d1qskxceo899me.cloudfront.net** — six minds at cycle 24 of 24. Read Goldfish and Present-Minded side by side. Same event, same model, same budget.
2. **/graveyard?sort=oldest** — the first memory any mind lost was its own name, at cycle 4, because it was the oldest thing it held. You can read it; the agent cannot.
3. **/echoes?category=partial_reconstruction** — a new memory that sits closer to something the mind has lost than to anything it still holds. Read the caveat above the list: it is a distance, not an access.
4. **/interviews?cycle=24&question=q01** — "Who are you?", answered by all six. Three give the name. One gives the model's default assistant persona. Switch the checkpoint to cycle 0: at the start, all six answered identically.

## Five minutes

5. **`prediction-scorecard.md`** — eight predictions registered before the run. Two failed. The one that most limits the conclusions is P8: the random control finished above three of the five designed mechanisms, so at n=1 the ranking cannot be separated from chance. That is stated in the README, in the article, and on the site.
6. **`results-summary.md`** — every mind, every checkpoint, every number, with the API endpoint each came from.
7. **`proof-of-aws-deployment.md`** — the schedule, the invocation counts, the disarmed state, each beside the command that reported it.
8. **`architecture.svg`** — eleven AWS services. Both switches that let a cycle run are currently off.

## What to be sceptical about, and where the answer is

| Doubt | Where it is answered |
| --- | --- |
| "These screenshots could be fixtures." | `SCREENSHOT_CAPTURE_REPORT.md`. The capture script refuses any page rendering the simulation banner and any run that is not `aws_canonical` at 24/24. |
| "The results could have been edited." | Snapshots are written under `attribute_not_exists` and hashed. `checksums.sha256` in the published dataset verifies with nothing from the repository. |
| "The protocol could have been changed to fit the result." | It is frozen by digest. Editing a protocol file makes the next run refuse to start. The manifest digest is `sha256:8e218e488c8745f4…` and is published. |
| "The model might have known which arm it was." | Arm names exist in one frontend file. A test asserts on every request that no arm name reached a prompt. |
| "Is this one run being oversold?" | It says n=1 in the README, in the article, on the methodology page, and in the footer of every chart. |
| "Did it actually run autonomously?" | 95 EventBridge-triggered Lambda invocations, 0 errors, producing 24 committed cycles. CloudWatch numbers are in `proof-of-aws-deployment.md`. |

## What it does not claim

It studies explicit memory records the application owns. It does not touch or observe the
model's KV cache, attention matrices, or hidden state, and it says nothing about them. The
agents are fictional and are not conscious. One run, one model, one seed world: the
mechanisms diverged, and by how much is not established.

## If you want to run it

`make bootstrap && make local-all` reproduces the whole pipeline on fixtures, with no AWS
account, in about a second. Everything it produces is labelled `LOCAL_FIXTURE` and
non-canonical — deliberately, and enforced in code.

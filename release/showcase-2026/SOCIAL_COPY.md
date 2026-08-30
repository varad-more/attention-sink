# Social copy

Every number below is on the live site. Nothing here says "best policy" and nothing here
says the model recalled a deleted memory, because neither is true.

## Short (X / Bluesky, ~270 characters)

> Six AI agents. Same memories, same events, same model, same token budget.
>
> The only difference: what each throws away when memory runs out.
>
> The first thing one of them forgot was its own name. 20 cycles later it introduced
> itself as "an AI system built by Amazon."
>
> https://d1qskxceo899me.cloudfront.net

## Thread (five posts)

**1/** Six generative agents. Identical seeds, identical events, identical model,
identical 208-token memory budget. One variable: the rule each uses to decide what to
forget. It ran 24 cycles on AWS. Everything is public.

**2/** The first memory any of them lost was its own name — "My name is Mara Venn."
Evicted at cycle 4 by the FIFO agent because it was the oldest thing it held and nothing
in that rule protects a name. You can still read it. The agent can't.

**3/** At cycle 24 they were asked "who are you?" Three still gave the name. The FIFO
agent said: "I am an AI system built by a team of inventors at Amazon." Identity drift
from its own cycle-0 answers: 0.929. The best arm's: 0.158.

**4/** The honest headline: the *random* control beat three of the five designed
mechanisms. One run, one seed — so the ranking can't be separated from chance. Two of my
eight preregistered predictions failed. All of that is published.

**5/** EventBridge Scheduler → Lambda → Bedrock → DynamoDB, six arms committed atomically
per cycle, CloudFront + a read-only API in front. 1,429 model calls, ~$0.20. Dataset
downloadable with checksums.
https://github.com/varad-more/attention-sink

## LinkedIn

> **What does an AI agent become when you take its memories away one at a time?**
>
> I built six agents that start identical — same twelve seed memories, same twenty-four
> events, same model, same 208-token budget — and differ in exactly one thing: the rule
> each uses to decide what to forget when the budget runs out. FIFO, LRU,
> citation-weighted, pinned-origin, random, and lossy summarisation.
>
> They ran twenty-four cycles on AWS. Then I asked all six the same question.
>
> The first memory any of them lost was its own name, evicted at cycle four because it
> was the oldest thing it held. Twenty cycles later that agent introduced itself as "an
> AI system built by a team of inventors at Amazon." Three of its siblings, same model,
> same day, still knew who they were.
>
> Two things I want to be straight about. First, this studies explicit memory records my
> application owns — not anything inside the model. Second, the random control beat three
> of the five designed mechanisms, so at one run per arm I cannot claim any mechanism is
> better. Two of my eight preregistered predictions failed, and I published the grading.
>
> EventBridge Scheduler, Lambda, Bedrock, DynamoDB, CloudFront. 1,429 model calls, about
> twenty cents. The whole dataset is downloadable with checksums.
>
> https://d1qskxceo899me.cloudfront.net

## Alt text for the hero image

The Attention Sink exhibition at cycle 24 of 24, showing the run status bar and the six
minds — Goldfish, Present-Minded, Pragmatist, Keeper of the First Day, Gambler and
Dreamer — each with the journal entry it wrote from the same event.

## Do not post

- Any framing that implies the model accessed a deleted memory.
- Any ranking of the six mechanisms as better or worse. n=1, and the random control outperformed three of them.
- "Attention sink" as a claim about model internals. It is a metaphor and the project says so on its own methodology page.

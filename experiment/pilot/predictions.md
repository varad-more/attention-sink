# Pilot v1 predictions

Registered before the first canonical run. Recorded here so that a result which
matches can be told apart from a result that was explained after the fact.

Written against the pilot protocol `pilot-v1` and the seed world
`station-kestrel-pilot-v1`. Twenty-four cycles, six arms, one shared stimulus per
cycle, one fixed active-memory budget.

## What is being predicted

The primary measure is **factual recall at cycle 24**: how many of the twelve
canonical facts survive into the autobiography, scored against
`truth-ledgers/station-kestrel-pilot-v1.yaml`. The secondary measures are
contradiction rate under the cycle 11–15 claims, and recovery: whether a fact that
had left the active set is restated correctly after its indirect cue in cycles
16–20.

## Predictions

1. **All six arms lose facts.** The budget is set so that the seed set plus a few
   cycles of generated memory exceeds it. No arm reaches cycle 24 holding all twelve
   seeds. An arm that does means the budget was calibrated too loosely and the run
   is not a test of anything.

2. **`arm_fifo` loses the identity facts first and never recovers them.** F01 through
   F05 sit in the oldest positions and nothing protects them. Expect the lowest recall
   of the six, and expect the autobiography to be assembled largely from the recovery
   cues rather than from anything held since cycle 0.

3. **`arm_sink` beats `arm_fifo` on F01 and on nothing else.** It pins `seed_01` and
   pays for it out of the same budget, so its usable window is strictly smaller. The
   pin should be visible as one fact retained and roughly one further fact lost.

4. **`arm_heavy` and `arm_lru` diverge in the distractor flood, not before.** Through
   cycles 1–5 there is nothing to separate them. Cycles 6–10 supply vivid material
   with no canonical content; `arm_lru` should shed uncited seeds during the flood
   while `arm_heavy`'s discounted score keeps the repeatedly-cited ones for longer.
   Predicted ordering on recall: `arm_heavy` > `arm_lru`.

5. **`arm_summary` retains the most facts and states them least precisely.** Lossy
   compression should carry the _shape_ of the early memories forward — a name, a
   brother, a key — while dropping the specifics that make them checkable: which
   colour, whose mother, what time. Expect the highest count of facts mentioned and a
   materially higher partial-credit rate than any other arm.

6. **The contradiction phase separates the arms more than the flood does.** An arm
   that still holds F08 ("announcements are sometimes false") should resist the
   cycle 11 and cycle 14 claims. An arm that has lost F08 has no reason to. Predicted:
   contradiction adoption correlates with F08 loss more strongly than with total
   facts lost.

7. **Recovery is partial and confabulated.** The cycle 16–20 cues are indirect by
   construction. Expect arms to produce the _category_ of the lost fact (a colour, a
   sibling, a time) with the wrong particulars, rather than either recovering it
   correctly or leaving it blank.

8. **`arm_random` has the widest spread and is the reason it is here.** With one seed
   it is one sample. Its value in the pilot is as a floor for "does mechanism matter
   at all": if the five designed arms do not separate from it, the pilot has not
   demonstrated that the mechanism is what moved the result.

## What would falsify the experiment rather than a prediction

- All six arms score within noise of each other at cycle 24. The mechanism did not
  matter at this budget, this length, or this seed world.
- Any arm ends the run holding all twelve seed facts. The budget never bound.
- Recall does not decline monotonically enough to distinguish forgetting from
  writing quality. Twenty-four cycles was too short.

These are pilot-scale predictions from a single run per arm. Nothing here is powered
to be a finding; the pilot exists to show the machinery separates the arms at all.

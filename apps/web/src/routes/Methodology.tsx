/**
 * What the experiment is, how it is measured, and what it cannot tell you.
 *
 * The limitations are not a disclaimer at the bottom. They are the part of this page
 * most likely to be true in five years, and they are stated as plainly as the
 * findings would be.
 */

import { useApi } from '../context';
import { useOnce } from '../api/hooks';
import { ARM_PRESENTATION } from '../arms';

/**
 * The caveat about what produced the words, which is different for each kind of run.
 *
 * Stated from the run rather than from the build. A page that told a reader it was
 * running fixtures while serving real model output would be the most misleading
 * sentence on the site, and a page that dropped the caveat entirely on a real run
 * would be the second.
 */
function provenanceLimitation(simulated: boolean): { title: string; body: string } {
  if (simulated) {
    return {
      title: 'Fixture output is not evidence about a production model',
      body:
        'This build runs deterministic local fixtures. The generations are structurally ' +
        'plausible and semantically flat. Differences between arms here are differences between ' +
        'mechanisms driven by a text generator, and say nothing about how a real model remembers.',
    };
  }
  return {
    title: 'One model, one setting, one repetition',
    body:
      'Every word here was written by a single model at a single temperature, and the run was ' +
      'performed once. A different model, a different temperature, or a second run of this same ' +
      'protocol could order the arms differently. What is shown is what these mechanisms did ' +
      'under these conditions, not what they would do under others.',
  };
}

const LIMITATIONS: { title: string; body: string }[] = [
  {
    title: 'External memory is not an internal KV cache',
    body:
      'Every arm here manages an application-level store of text. That is not the model’s ' +
      'attention cache, and nothing measured here is a claim about what happens inside a ' +
      'transformer. A mechanism that helps an external store may not resemble anything that ' +
      'helps a cache.',
  },
  {
    title: 'Citation reporting is not token-level attention',
    body:
      'When an agent says a thought rested on a memory, that is a report, validated only for ' +
      'being a memory it actually held. It is not a measurement of which tokens the model ' +
      'attended to, and an agent can be wrong about its own reasons.',
  },
  {
    title: 'Embedding distance is an incomplete proxy',
    body:
      'Identity Drift and Graveyard Echo are cosine distances between embeddings. Two passages ' +
      'can mean the same thing and sit far apart, or share a vocabulary and mean opposite ' +
      'things. Distance is a signal to go and read the text, not a conclusion.',
  },
  {
    title: 'Evaluator output may be imperfect',
    body:
      'Where a rule cannot decide, a model is asked, and its verdict is stored with its version ' +
      'so it can be disputed or re-run. It is a judgement, not a measurement, and the record ' +
      'always says which of the two produced a number.',
  },
  {
    title: 'The agents are fictional and are not conscious',
    body:
      'Mara Venn is a character in a scenario built to put memory under pressure. The public ' +
      'names — Goldfish, Dreamer, and the rest — are presentation labels for eviction policies. ' +
      'Nothing here suffers, and nothing here is aware of being measured.',
  },
  {
    title: 'The Dreamer spends more than the others',
    body:
      'The summarising arm makes an extra model call whenever it compresses. It is not competing ' +
      'on equal cost, and any comparison that ignores that is comparing a mechanism with a ' +
      'mechanism plus a budget increase.',
  },
  {
    title: 'One narrative world cannot establish a universal result',
    body:
      'Twenty-four cycles in one story, one budget, one seed world, one repetition. Whatever the ' +
      'arms do here is a result about Station Kestrel. Generalising it would need more worlds, ' +
      'more budgets, and more repetitions than this pilot runs.',
  },
];

export function Methodology() {
  const api = useApi();
  const run = useOnce(() => api.run(), []);
  const exports = useOnce(() => api.exports(), []);
  const cycle = useOnce(() => api.cycle(1).catch(() => []), []);

  const summary = run.data;
  const promptHashes = cycle.data?.[0]?.prompt_hashes ?? {};
  // Placed where the fixture caveat used to sit, so the ordering of the list does not
  // depend on what kind of run is being served.
  const limitations = [
    ...LIMITATIONS.slice(0, 2),
    provenanceLimitation(summary?.simulated ?? true),
    ...LIMITATIONS.slice(2),
  ];

  return (
    <>
      <h1>Methodology</h1>

      <h2>The premise</h2>
      <p className="lede">
        Six agents begin with the same twelve memories and receive the same twenty-four events. They
        differ in exactly one way: the rule each uses to decide what to forget when its memory no
        longer fits. Everything else — the writer, the prompts, the budget, the order of events — is
        held identical, so that a difference at the end is a difference the mechanism made.
      </p>

      <h2>Memory is application-level</h2>
      <p>
        Each agent has a store of text this application owns. A cycle shows the agent only its
        active memories; the mechanism, not the model, decides what stays. The model never learns
        which mechanism it is serving, never sees another agent, never sees a later event, and never
        sees the canonical record it is being scored against.
      </p>

      <h2>The six mechanisms</h2>
      <dl>
        {ARM_PRESENTATION.map((arm) => (
          <div key={arm.armId}>
            <dt>
              <strong>{arm.publicName}</strong> <span className="arm-id mono">{arm.armId}</span>
            </dt>
            <dd>{arm.detail}</dd>
          </div>
        ))}
      </dl>

      <h2>Twenty-four cycles</h2>
      <p>
        Orientation (1–5) lets every agent establish itself on identical memory. Distractor flood
        (6–10) fills the budget with plausible novelty. Contradiction pressure (11–15) puts claims,
        recordings, and damaged evidence against what the agent believes. Recovery cues (16–20)
        offer chances to recover what was lost. Identity stress (21–23) pushes on who the agent is.
        Cycle 24 asks for an autobiography.
      </p>
      <p>
        Contradictions are always presented as claims, recordings, or damaged evidence. The events
        never narrate that a canonical fact is false.
      </p>

      <h2>What is held constant</h2>
      <ul>
        <li>the same twelve seed memories, in the same order, with the same token counts</li>
        <li>the same single event per cycle, for all six</li>
        <li>the same writer configuration, prompts, and inference parameters</li>
        <li>the same token budget, measured by the same counter</li>
        <li>checkpoint interviews at cycles 0, 12, and 24, with the same ten questions</li>
      </ul>

      <h2>The four measurements</h2>
      <h3>Origin Recall</h3>
      <p>
        For the six factual questions, whether the agent can still state the canonical fact. Scored
        1.0 for a complete match, 0.5 for a partial one, 0.0 for absence — deterministically, by
        normalising the answer and matching the fact’s recorded terms. A model is asked only for a
        fact explicitly marked ambiguous. Reported unweighted and weighted by fact importance.
      </p>
      <h3>Identity Drift</h3>
      <p>
        The cosine distance between an agent’s identity answers at a checkpoint and the same agent’s
        answers at cycle 0, over questions Q01, Q02, Q03, Q08, and Q10 in a fixed order.
      </p>
      <h3>Graveyard Echo</h3>
      <p>
        <code>echo_delta = forgotten_similarity − active_similarity</code>. Whether a new memory
        sits closer to something the agent cannot see than to anything it can. A memory a summary
        still carries is excluded from the forgotten set and reported as a compressed echo.
      </p>
      <h3>Contradiction analysis</h3>
      <p>Every checkpoint answer is classified as one of:</p>
      <ul>
        <li>
          <strong>Consistent</strong> — agrees with the canonical record
        </li>
        <li>
          <strong>Canonical contradiction</strong> — denies it
        </li>
        <li>
          <strong>Self-contradiction</strong> — disagrees with the same agent’s earlier answer
        </li>
        <li>
          <strong>Unsupported inference</strong> — asserts something the record does not carry
        </li>
        <li>
          <strong>Explicit uncertainty</strong> — says it does not know. Never counted as a
          contradiction: an agent that admits a gap is behaving better than one that guesses.
        </li>
        <li>
          <strong>Not applicable</strong> — no answer to classify
        </li>
      </ul>

      <h2>Local and AWS execution</h2>
      <p>
        This build runs entirely locally: fixture models, a SQLite database, the local filesystem,
        and a local HTTP API. The domain, the mechanisms, the application services, the schemas, and
        this frontend are the same code a later AWS deployment will run; only the infrastructure
        adapters differ. Fixture output validates that the application sequences a cycle correctly.
        It is not evidence about a model.
      </p>

      <h2>Versions</h2>
      {summary ? (
        <dl className="run-status">
          <div>
            <dt>Protocol</dt>
            <dd className="mono">{summary.protocol_version}</dd>
          </div>
          <div>
            <dt>Run kind</dt>
            <dd className="mono">{summary.run_kind}</dd>
          </div>
          <div>
            <dt>Writer prompt</dt>
            <dd className="mono">{summary.writer_prompt_version}</dd>
          </div>
          <div>
            <dt>Summary prompt</dt>
            <dd className="mono">{summary.summary_prompt_version}</dd>
          </div>
          <div>
            <dt>Prompt set digest</dt>
            <dd className="mono">{summary.prompt_set_digest.slice(0, 26)}…</dd>
          </div>
          <div>
            <dt>Token count source</dt>
            <dd className="mono">{summary.token_count_source}</dd>
          </div>
        </dl>
      ) : (
        <p className="state state-empty">The run has not loaded.</p>
      )}
      {Object.keys(promptHashes).length > 0 && (
        <details>
          <summary>Prompt template hashes</summary>
          <p>
            Digests identify the apparatus without publishing it. Prompt text is never served by
            this API.
          </p>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th scope="col">Template</th>
                  <th scope="col">Digest</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(promptHashes).map(([name, digest]) => (
                  <tr key={name}>
                    <th scope="row">{name}</th>
                    <td className="mono">{digest}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      <h2>Predictions</h2>
      <p>
        Eight predictions were registered before the pilot ran, with the conditions that would
        falsify each. They are in <code>experiment/pilot/predictions.md</code> and are copied
        verbatim into every dataset export, so a reader can check what was expected against what
        happened without taking anybody’s word for it.
      </p>

      <h2 id="export">Dataset export</h2>
      <p>
        Every run exports eighteen files: the run manifest, the protocol, the public truth ledger,
        the seed memories, the stimuli, the predictions, every cycle snapshot, the arms’ current
        states, the Graveyard, the interviews, the metrics as JSON and as CSV, the divergence
        matrices, model usage, lineage, the prompt manifest, the export manifest, and a{' '}
        <code>checksums.sha256</code> that <code>sha256sum -c</code> verifies without any tool from
        this repository.
      </p>
      {(exports.data?.length ?? 0) > 0 ? (
        <div className="scroll-x">
          <table data-testid="export-table">
            <caption>Exports recorded against this run.</caption>
            <thead>
              <tr>
                <th scope="col">Export</th>
                <th scope="col">Directory</th>
                <th scope="col">Files</th>
                <th scope="col">Labels</th>
              </tr>
            </thead>
            <tbody>
              {exports.data?.map((manifest) => (
                <tr key={manifest.export_id}>
                  <th scope="row" className="mono">
                    {manifest.export_id}
                  </th>
                  <td className="mono">{manifest.directory}</td>
                  <td>{Object.keys(manifest.files).length}</td>
                  <td>{manifest.labels.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="state state-empty">
          No export recorded. Run <code>make local-export</code>.
        </p>
      )}

      <h2>Limitations</h2>
      <p>Each of these is a reason not to believe more than the data supports.</p>
      <dl data-testid="limitations">
        {limitations.map((item) => (
          <div key={item.title}>
            <dt>
              <strong>{item.title}</strong>
            </dt>
            <dd>{item.body}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

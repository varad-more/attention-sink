/**
 * The exhibition's front door, and the same view for any historical cycle.
 *
 * The cycle a reader is looking at is the one thing this page will not change under
 * them. The live view polls; a selected historical cycle is fetched once and frozen,
 * because a reader who navigated to cycle 7 is reading cycle 7.
 */

import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { orderArms } from '../arms';
import { useApi, useAppConfig } from '../context';
import { useOnce, usePolled } from '../api/hooks';
import type { CycleView, EchoRow, MetricRow, RunSummary } from '../api/types';
import { ErrorState, Loading } from '../components/States';
import { MindCard, readableReason } from '../components/MindCard';

function latestPerArm(rows: MetricRow[], upTo: number): Map<string, MetricRow> {
  const best = new Map<string, MetricRow>();
  for (const row of rows) {
    if (row.cycle > upTo) continue;
    const held = best.get(row.arm_id);
    if (!held || row.cycle >= held.cycle) best.set(row.arm_id, row);
  }
  return best;
}

export function SixMinds() {
  const api = useApi();
  const config = useAppConfig();
  const params = useParams();
  const pinnedCycle = params.cycle ? Number.parseInt(params.cycle, 10) : null;
  const historical = pinnedCycle !== null && Number.isFinite(pinnedCycle);

  const run = usePolled<RunSummary>(() => api.run(), [], {
    intervalMs: config.pollIntervalMs,
    enabled: !historical,
  });

  const cycle = historical ? pinnedCycle : (run.data?.current_cycle ?? 0);
  const views = useOnce<CycleView[]>(
    () => (cycle > 0 ? api.cycle(cycle) : Promise.resolve([])),
    [cycle],
  );
  const arms = useOnce(() => api.arms(), [run.data?.current_cycle ?? 0]);
  const recall = useOnce(() => api.metrics({ metricName: 'origin_recall' }), []);
  const drift = useOnce(() => api.metrics({ metricName: 'identity_drift' }), []);
  const echoes = useOnce(() => api.echoes(), []);

  const [focus, setFocus] = useState<string | null>(null);

  const byArm = useMemo(
    () => new Map((views.data ?? []).map((view) => [view.arm_id, view])),
    [views.data],
  );
  const activeCounts = useMemo(
    () => new Map((arms.data ?? []).map((arm) => [arm.arm_id, arm.active_memory_count])),
    [arms.data],
  );
  const recallByArm = useMemo(
    () => latestPerArm(recall.data?.items ?? [], cycle),
    [recall.data, cycle],
  );
  const driftByArm = useMemo(
    () => latestPerArm(drift.data?.items ?? [], cycle),
    [drift.data, cycle],
  );
  const echoByArm = useMemo(() => {
    const best = new Map<string, EchoRow>();
    for (const row of echoes.data?.items ?? []) {
      if (row.cycle > cycle) continue;
      const held = best.get(row.arm_id);
      if (!held || row.cycle >= held.cycle) best.set(row.arm_id, row);
    }
    return best;
  }, [echoes.data, cycle]);

  // The heading is rendered before the data resolves, and before any failure. A page
  // whose h1 depends on a successful fetch is a page with no heading exactly when a
  // reader most needs to know where they are.
  if (run.status !== 'ready') {
    return (
      <>
        <h1>Six minds. One past. No room.</h1>
        <p className="lede">
          Six identical agents began with the same memories. Every new thought forced each one to
          decide what part of its past could remain.
        </p>
        {run.status === 'error' ? (
          <ErrorState error={run.error} what="the run" />
        ) : (
          <Loading what="the run" />
        )}
      </>
    );
  }

  const summary = run.data;
  const analysisPending = (recall.data?.items.length ?? 0) === 0;
  const presented = orderArms(summary.arms);
  const shown = focus ? presented.filter((arm) => arm.armId === focus) : presented;

  return (
    <>
      <h1>Six minds. One past. No room.</h1>
      <p className="lede">
        Six identical agents began with the same memories. Every new thought forced each one to
        decide what part of its past could remain.
      </p>

      <dl className="run-status" data-testid="run-status">
        <div>
          <dt>Cycle</dt>
          <dd data-testid="current-cycle">
            {cycle} of {summary.maximum_cycles}
          </dd>
        </div>
        <div>
          <dt>Run status</dt>
          <dd>{summary.status}</dd>
        </div>
        <div>
          <dt>Analysis</dt>
          <dd>{analysisPending ? 'pending' : 'scored'}</dd>
        </div>
        <div>
          <dt>Last completed cycle</dt>
          <dd>{summary.current_cycle}</dd>
        </div>
        <div>
          <dt>Budget</dt>
          <dd>
            {summary.memory_budget_tokens} tokens
            <span className="arm-id"> {summary.token_count_source}</span>
          </dd>
        </div>
      </dl>

      {historical && (
        <p className="state state-empty" role="status">
          Showing cycle {cycle}, which is frozen. <Link to="/">Return to the live view</Link>.
        </p>
      )}
      {run.stale && (
        <p className="state" role="status">
          The API stopped answering; showing the last figures that loaded.
        </p>
      )}

      <nav className="controls" aria-label="Cycle and view controls">
        <label>
          Cycle
          <select
            value={cycle}
            onChange={(event) => {
              const next = Number(event.target.value);
              window.location.assign(next === summary.current_cycle ? '/' : `/cycle/${next}`);
            }}
            data-testid="cycle-selector"
          >
            {Array.from({ length: summary.current_cycle + 1 }, (_, index) => index)
              .filter((value) => value > 0)
              .reverse()
              .map((value) => (
                <option key={value} value={value}>
                  Cycle {value}
                </option>
              ))}
          </select>
        </label>
        {focus && (
          <button
            type="button"
            onClick={() => {
              setFocus(null);
            }}
          >
            Show all six
          </button>
        )}
        <Link to="/graveyard">Graveyard</Link>
        <Link to="/interviews">Interviews</Link>
        <Link to="/methodology">Methodology</Link>
        <Link to="/methodology#export">Dataset export</Link>
      </nav>

      {views.status === 'error' && <ErrorState error={views.error} what={`cycle ${cycle}`} />}
      {views.status === 'loading' && <Loading what={`cycle ${cycle}`} />}

      <section className="minds" aria-label="The six minds">
        {shown.map((arm) => (
          <MindCard
            key={arm.armId}
            arm={arm}
            cycle={byArm.get(arm.armId) ?? null}
            activeMemoryCount={activeCounts.get(arm.armId) ?? null}
            originRecall={recallByArm.get(arm.armId) ?? null}
            identityDrift={driftByArm.get(arm.armId) ?? null}
            echo={echoByArm.get(arm.armId) ?? null}
            analysisPending={analysisPending}
            focused={focus === arm.armId}
            onFocus={() => {
              setFocus(focus === arm.armId ? null : arm.armId);
            }}
          />
        ))}
      </section>

      <h2 id="comparison">Side by side</h2>
      <div className="scroll-x">
        <table data-testid="comparison-table">
          <caption>
            Every arm at cycle {cycle}. Figures are recorded; the reason is the mechanism&rsquo;s
            own decision code.
          </caption>
          <thead>
            <tr>
              <th scope="col">Mind</th>
              <th scope="col">Active</th>
              <th scope="col">Tokens</th>
              <th scope="col">Retired</th>
              <th scope="col">Compressed</th>
              <th scope="col">Recall</th>
              <th scope="col">Drift</th>
              <th scope="col">Reason</th>
            </tr>
          </thead>
          <tbody>
            {presented.map((arm) => {
              const view = byArm.get(arm.armId);
              const recallRow = recallByArm.get(arm.armId);
              const driftRow = driftByArm.get(arm.armId);
              return (
                <tr key={arm.armId}>
                  <th scope="row">
                    {arm.publicName} <span className="arm-id mono">{arm.armId}</span>
                  </th>
                  <td>{activeCounts.get(arm.armId) ?? '—'}</td>
                  <td>{view ? `${view.tokens_after}/${view.budget_tokens}` : '—'}</td>
                  <td>{view ? view.retired_memory_ids.length : '—'}</td>
                  <td>{view ? view.compressed_memory_ids.length : '—'}</td>
                  <td>{recallRow ? recallRow.value.toFixed(2) : 'pending'}</td>
                  <td>{driftRow ? driftRow.value.toFixed(3) : 'pending'}</td>
                  <td>{view ? view.policy_decision_codes.map(readableReason).join(' ') : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

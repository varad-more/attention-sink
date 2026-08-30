/**
 * Twenty-four cycles, six tracks, and what happened on each.
 *
 * The chart is an accessible SVG with a table underneath saying the same thing. The
 * table is not a fallback nobody reads -- it is the primary record for anyone using a
 * screen reader, and the scrubber and the table always agree.
 *
 * The divergence figures are geometric distance between identity documents. The page
 * says so, in the page, because a chart of distances is exactly the kind of thing a
 * reader will otherwise take as a causal claim.
 */

import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { orderArms } from '../arms';
import { useApi } from '../context';
import { useOnce } from '../api/hooks';
import type { CycleView } from '../api/types';
import { ErrorState, Loading } from '../components/States';

interface TrackPoint {
  cycle: number;
  created: number;
  evicted: number;
  compressed: number;
  tokens: number;
  budget: number;
  echo: boolean;
}

const ROW_HEIGHT = 34;
const LEFT = 132;
const STEP = 26;

export function Timeline() {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const run = useOnce(() => api.run(), []);
  const echoes = useOnce(() => api.echoes(), []);
  const divergence = useOnce(() => api.divergence(), []);
  const contradictions = useOnce(() => api.contradictions(), []);

  const maxCycle = run.data?.current_cycle ?? 0;
  const selected = Number(params.get('cycle') ?? maxCycle) || maxCycle;
  const [cycles, setCycles] = useState<Map<number, CycleView[]>>(new Map());

  const loaded = useOnce(async () => {
    const all = new Map<number, CycleView[]>();
    for (let cycle = 1; cycle <= maxCycle; cycle += 1) {
      all.set(cycle, await api.cycle(cycle));
    }
    setCycles(all);
    return all;
  }, [maxCycle]);

  const echoCycles = useMemo(() => {
    const map = new Map<string, Set<number>>();
    for (const row of echoes.data?.items ?? []) {
      if (row.echo_delta < row.threshold) continue;
      const held = map.get(row.arm_id) ?? new Set<number>();
      held.add(row.cycle);
      map.set(row.arm_id, held);
    }
    return map;
  }, [echoes.data]);

  const tracks = useMemo(() => {
    const byArm = new Map<string, TrackPoint[]>();
    for (const [cycle, views] of [...cycles.entries()].sort((a, b) => a[0] - b[0])) {
      for (const view of views) {
        const points = byArm.get(view.arm_id) ?? [];
        points.push({
          cycle,
          created: 1,
          evicted: view.retired_memory_ids.length - view.compressed_memory_ids.length,
          compressed: view.compressed_memory_ids.length,
          tokens: view.tokens_after,
          budget: view.budget_tokens,
          echo: echoCycles.get(view.arm_id)?.has(cycle) ?? false,
        });
        byArm.set(view.arm_id, points);
      }
    }
    return byArm;
  }, [cycles, echoCycles]);

  if (run.status !== 'ready') {
    return (
      <>
        <h1>Timeline</h1>
        {run.status === 'error' ? (
          <ErrorState error={run.error} what="the run" />
        ) : (
          <Loading what="the run" />
        )}
      </>
    );
  }

  const arms = orderArms(run.data.arms);
  const checkpoints = run.data.checkpoint_cycles;
  const width = LEFT + (maxCycle + 1) * STEP + 20;
  const height = arms.length * ROW_HEIGHT + 46;

  const setCycle = (value: number) => {
    const next = new URLSearchParams(params);
    next.set('cycle', String(value));
    setParams(next, { replace: true });
  };

  return (
    <>
      <h1>Timeline</h1>
      <p className="lede">
        What each mind created, evicted, and compressed, cycle by cycle. Distances at the
        checkpoints are the geometric distance between stored identity answers — they show that
        answers moved apart, not why, and not that one thing caused another.
      </p>

      <div className="controls">
        <label>
          Cycle {selected} of {maxCycle}
          <input
            type="range"
            min={1}
            max={Math.max(maxCycle, 1)}
            value={selected}
            onChange={(event) => {
              setCycle(Number(event.target.value));
            }}
            aria-label={`Cycle scrubber, showing cycle ${selected} of ${maxCycle}`}
            data-testid="timeline-scrubber"
          />
        </label>
        <Link to={`/cycle/${selected}`}>Open cycle {selected}</Link>
      </div>

      {loaded.status === 'loading' && <Loading what="every completed cycle" />}

      <figure className="timeline-figure">
        <figcaption className="visually-hidden">
          Six tracks, one per mind, from cycle 1 to {maxCycle}. A filled square marks a cycle in
          which the mind retired something; a ring marks a compression; a vertical rule marks a
          checkpoint. The table below carries the same figures.
        </figcaption>
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={`Activity for six minds across ${maxCycle} cycles`}
        >
          {checkpoints
            .filter((cycle) => cycle > 0 && cycle <= maxCycle)
            .map((cycle) => (
              <line
                key={`cp-${cycle}`}
                x1={LEFT + cycle * STEP}
                x2={LEFT + cycle * STEP}
                y1={8}
                y2={arms.length * ROW_HEIGHT + 12}
                stroke="#b9b2a5"
                strokeDasharray="3 3"
              />
            ))}
          <line
            x1={LEFT + selected * STEP}
            x2={LEFT + selected * STEP}
            y1={4}
            y2={arms.length * ROW_HEIGHT + 16}
            stroke="#1a4fd6"
            strokeWidth={2}
          />
          {arms.map((arm, row) => {
            const y = row * ROW_HEIGHT + 30;
            const points = tracks.get(arm.armId) ?? [];
            return (
              <g key={arm.armId}>
                <text x={4} y={y + 4} fontSize={12}>
                  {arm.publicName}
                </text>
                <line x1={LEFT} x2={width - 20} y1={y} y2={y} stroke="#e2ded4" />
                {points.map((point) => {
                  const x = LEFT + point.cycle * STEP;
                  return (
                    <g key={point.cycle}>
                      {point.evicted > 0 && (
                        <rect x={x - 4} y={y - 4} width={8} height={8} fill="#8a3d18" />
                      )}
                      {point.compressed > 0 && (
                        <circle cx={x} cy={y} r={5} fill="none" stroke="#2c5f3f" strokeWidth={2} />
                      )}
                      {point.echo && (
                        <polygon
                          points={`${x},${y - 11} ${x - 4},${y - 5} ${x + 4},${y - 5}`}
                          fill="#7a4b2a"
                        />
                      )}
                    </g>
                  );
                })}
              </g>
            );
          })}
          {Array.from({ length: maxCycle }, (_, index) => index + 1)
            .filter((cycle) => cycle % 4 === 0 || cycle === 1)
            .map((cycle) => (
              <text
                key={`x-${cycle}`}
                x={LEFT + cycle * STEP}
                y={arms.length * ROW_HEIGHT + 34}
                fontSize={11}
                textAnchor="middle"
              >
                {cycle}
              </text>
            ))}
        </svg>
      </figure>

      <h2>Cycle {selected}, in figures</h2>
      <div className="scroll-x">
        <table data-testid="timeline-table">
          <caption>The same information as the chart above, for cycle {selected}.</caption>
          <thead>
            <tr>
              <th scope="col">Mind</th>
              <th scope="col">Created</th>
              <th scope="col">Evicted</th>
              <th scope="col">Compressed</th>
              <th scope="col">Tokens used</th>
              <th scope="col">Echo above threshold</th>
            </tr>
          </thead>
          <tbody>
            {arms.map((arm) => {
              const point = (tracks.get(arm.armId) ?? []).find((p) => p.cycle === selected);
              return (
                <tr key={arm.armId}>
                  <th scope="row">{arm.publicName}</th>
                  <td>{point ? point.created : '—'}</td>
                  <td>{point ? point.evicted : '—'}</td>
                  <td>{point ? point.compressed : '—'}</td>
                  <td>{point ? `${point.tokens}/${point.budget}` : '—'}</td>
                  <td>{point ? (point.echo ? 'yes' : 'no') : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h2>Divergence at the checkpoints</h2>
      <p>
        Cosine distance between each pair of identity documents. Zero on the diagonal by
        construction. A larger number means two minds answered the identity questions less alike; it
        does not say which mechanism caused it.
      </p>
      {Object.entries(divergence.data?.matrices ?? {}).length === 0 ? (
        <p className="state state-empty">
          No divergence has been computed. Run <code>make local-analyze</code>.
        </p>
      ) : (
        Object.entries(divergence.data?.matrices ?? {}).map(([cycle, matrix]) => (
          <div className="scroll-x" key={cycle}>
            <table>
              <caption>Cycle {cycle}</caption>
              <thead>
                <tr>
                  <th scope="col">Mind</th>
                  {Object.keys(matrix).map((armId) => (
                    <th scope="col" key={armId}>
                      {orderArms([armId])[0]?.publicName ?? armId}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(matrix).map(([armId, row]) => (
                  <tr key={armId}>
                    <th scope="row">{orderArms([armId])[0]?.publicName ?? armId}</th>
                    {Object.values(row).map((value, index) => (
                      <td key={index}>{value.toFixed(3)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      )}

      <h2>Contradictions at the checkpoints</h2>
      {(contradictions.data?.items.length ?? 0) === 0 ? (
        <p className="state state-empty">
          No contradiction analysis stored. Run <code>make local-analyze</code>.
        </p>
      ) : (
        <p>
          {contradictions.data?.items.filter((row) => row.label.endsWith('contradiction')).length}{' '}
          contradictory answers across {contradictions.data?.items.length} classified answers.
          Admitted uncertainty is never counted as a contradiction. See{' '}
          <Link to="/interviews">Interviews</Link>.
        </p>
      )}
    </>
  );
}

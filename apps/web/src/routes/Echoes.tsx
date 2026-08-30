/**
 * Measured resemblances between what a mind wrote and what it had lost.
 *
 * The language on this page is chosen and not incidental. Every heading says
 * "possible", "partial", or "shared motif"; nothing says an agent retrieved anything.
 * What was measured is a distance between two embeddings. That a later sentence sits
 * closer to a forgotten memory than to any remaining one is interesting; it is not
 * evidence that the forgotten memory was read.
 */

import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { orderArms, presentArm } from '../arms';
import { useApi } from '../context';
import { useOnce } from '../api/hooks';
import { Empty, ErrorState, Loading } from '../components/States';

const CATEGORY_LABEL: Record<string, string> = {
  genuine_reconstruction: 'Possible reconstruction',
  partial_reconstruction: 'Partial reconstruction',
  contradictory_reconstruction: 'Contradictory reconstruction',
  shared_motif_only: 'Shared motif only',
  unrelated: 'Unrelated',
  compressed_echo: 'Compressed echo — the summary still carries it',
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABEL[category] ?? category.replaceAll('_', ' ');
}

export function Echoes() {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const echoes = useOnce(() => api.echoes(), []);
  const graveyard = useOnce(() => api.graveyard({ limit: 200 }), []);
  const run = useOnce(() => api.run(), []);

  const arm = params.get('arm') ?? '';
  const category = params.get('category') ?? '';

  const forgottenText = useMemo(
    () => new Map((graveyard.data?.items ?? []).map((row) => [row.memory_id, row.text])),
    [graveyard.data],
  );

  const rows = useMemo(() => {
    let items = [...(echoes.data?.items ?? [])];
    if (arm) items = items.filter((row) => row.arm_id === arm);
    if (category) items = items.filter((row) => row.category === category);
    return items.sort((a, b) => b.echo_delta - a.echo_delta);
  }, [echoes.data, arm, category]);

  if (echoes.status !== 'ready') {
    return (
      <>
        <h1>Graveyard Echo</h1>
        {echoes.status === 'error' ? (
          <ErrorState error={echoes.error} what="the echoes" />
        ) : (
          <Loading what="the echoes" />
        )}
      </>
    );
  }

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const categories = [...new Set(echoes.data.items.map((row) => row.category))].sort();

  return (
    <>
      <h1>Graveyard Echo</h1>
      <p className="lede">
        For each new memory, how close it sits to something the mind can no longer see, compared
        with how close it sits to anything it still holds. A positive delta is the interesting case.{' '}
        <strong>It is a measured distance and not an access</strong>: nothing here shows that an
        agent read an evicted memory.
      </p>

      <form
        className="controls"
        aria-label="Filter echoes"
        onSubmit={(e) => {
          e.preventDefault();
        }}
      >
        <label>
          Mind
          <select
            value={arm}
            onChange={(event) => {
              setParam('arm', event.target.value);
            }}
          >
            <option value="">All six</option>
            {orderArms(run.data?.arms ?? []).map((item) => (
              <option key={item.armId} value={item.armId}>
                {item.publicName}
              </option>
            ))}
          </select>
        </label>
        <label>
          Classification
          <select
            value={category}
            onChange={(event) => {
              setParam('category', event.target.value);
            }}
          >
            <option value="">Any</option>
            {categories.map((value) => (
              <option key={value} value={value}>
                {categoryLabel(value)}
              </option>
            ))}
          </select>
        </label>
      </form>

      <p role="status">{rows.length} measured resemblances.</p>

      {rows.length === 0 ? (
        <Empty>Nothing matches those filters.</Empty>
      ) : (
        <ul className="entry-list" data-testid="echo-list">
          {rows.map((row) => (
            <li className="entry" key={`${row.arm_id}:${row.cycle}:${row.memory_id}`}>
              <header>
                <h2>
                  {presentArm(row.arm_id).publicName}, cycle {row.cycle}
                </h2>
                <p className="meta">{categoryLabel(row.category)}</p>
              </header>

              <h3>What it had lost</h3>
              <blockquote>
                {row.nearest_forgotten_memory_id
                  ? (forgottenText.get(row.nearest_forgotten_memory_id) ??
                    (row.evidence_excerpt || '—'))
                  : 'nothing forgotten yet'}
              </blockquote>

              <h3>What it wrote later</h3>
              <blockquote>
                <Link to={`/memory/${encodeURIComponent(row.memory_id)}`} className="mono">
                  {row.memory_id}
                </Link>
              </blockquote>

              <dl className="figures">
                <div>
                  <dt>Forgotten similarity</dt>
                  <dd>{row.forgotten_similarity.toFixed(3)}</dd>
                </div>
                <div>
                  <dt>Active similarity</dt>
                  <dd>{row.active_similarity.toFixed(3)}</dd>
                </div>
                <div>
                  <dt>Echo delta</dt>
                  <dd>{row.echo_delta.toFixed(3)}</dd>
                </div>
                <div>
                  <dt>Threshold</dt>
                  <dd>{row.threshold.toFixed(3)}</dd>
                </div>
                <div>
                  <dt>Cycle</dt>
                  <dd>
                    <Link to={`/cycle/${row.cycle}`}>{row.cycle}</Link>
                  </dd>
                </div>
                <div>
                  <dt>Decided by</dt>
                  <dd>{row.evaluator_version ?? 'rule only'}</dd>
                </div>
              </dl>

              {row.evidence_excerpt && (
                <details>
                  <summary>Evidence excerpt</summary>
                  <blockquote>{row.evidence_excerpt}</blockquote>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

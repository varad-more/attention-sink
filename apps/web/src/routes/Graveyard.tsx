/**
 * What each mind lost, and whether losing it cost anything.
 *
 * The distinction the whole view exists for is between *evicted* and *compressed*. A
 * memory a summary still carries has not been forgotten, and the page says so in
 * words rather than only in a colour -- a reader who cannot see the difference
 * between two greens would otherwise learn nothing.
 *
 * Filter state lives in the URL, so a link to "everything the Dreamer compressed
 * after cycle 12" is a link somebody can send.
 */

import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { orderArms, presentArm } from '../arms';
import { useApi } from '../context';
import { useOnce } from '../api/hooks';
import type { EchoRow, GraveyardView } from '../api/types';
import { Empty, ErrorState, Loading } from '../components/States';

type SortKey = 'newest' | 'oldest' | 'longest' | 'most_cited' | 'strongest_echo';

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'newest', label: 'Newest retirement' },
  { key: 'oldest', label: 'Oldest retirement' },
  { key: 'longest', label: 'Longest lifespan' },
  { key: 'most_cited', label: 'Most cited' },
  { key: 'strongest_echo', label: 'Strongest echo' },
];

export function statusTag(status: string) {
  const label =
    status === 'compressed' ? 'compressed' : status === 'superseded' ? 'superseded' : 'evicted';
  return <span className={`tag tag-${label}`}>{label}</span>;
}

export function Graveyard() {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const entries = useOnce(() => api.graveyard({ limit: 200 }), []);
  const echoes = useOnce(() => api.echoes(), []);
  const run = useOnce(() => api.run(), []);

  const arm = params.get('arm') ?? '';
  const status = params.get('status') ?? '';
  const kind = params.get('kind') ?? '';
  const cycle = params.get('cycle') ?? '';
  const echoState = params.get('echo') ?? '';
  const search = params.get('q') ?? '';
  // Validated rather than cast: the sort key comes from a URL a person can edit.
  const sort: SortKey = SORTS.find((option) => option.key === params.get('sort'))?.key ?? 'newest';

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const echoByMemory = useMemo(() => {
    const map = new Map<string, EchoRow>();
    for (const row of echoes.data?.items ?? []) {
      if (!row.nearest_forgotten_memory_id) continue;
      const held = map.get(row.nearest_forgotten_memory_id);
      if (!held || row.echo_delta > held.echo_delta) map.set(row.nearest_forgotten_memory_id, row);
    }
    return map;
  }, [echoes.data]);

  const filtered = useMemo(() => {
    let rows: GraveyardView[] = [...(entries.data?.items ?? [])];
    if (arm) rows = rows.filter((row) => row.arm_id === arm);
    if (status) rows = rows.filter((row) => row.status === status);
    if (kind) rows = rows.filter((row) => row.memory_type === kind);
    if (cycle) rows = rows.filter((row) => String(row.retirement_cycle) === cycle);
    if (echoState === 'with') rows = rows.filter((row) => echoByMemory.has(row.memory_id));
    if (echoState === 'without') rows = rows.filter((row) => !echoByMemory.has(row.memory_id));
    if (search) {
      const needle = search.toLowerCase();
      rows = rows.filter((row) => row.text.toLowerCase().includes(needle));
    }
    const strength = (row: GraveyardView) => echoByMemory.get(row.memory_id)?.echo_delta ?? -1;
    const comparators: Record<SortKey, (a: GraveyardView, b: GraveyardView) => number> = {
      newest: (a, b) => b.retirement_cycle - a.retirement_cycle,
      oldest: (a, b) => a.retirement_cycle - b.retirement_cycle,
      longest: (a, b) => b.lifespan - a.lifespan,
      most_cited: (a, b) => b.validated_citation_count - a.validated_citation_count,
      strongest_echo: (a, b) => strength(b) - strength(a),
    };
    return rows.sort(comparators[sort]);
  }, [entries.data, arm, status, kind, cycle, echoState, search, sort, echoByMemory]);

  if (entries.status !== 'ready') {
    return (
      <>
        <h1>Graveyard</h1>
        {entries.status === 'error' ? (
          <ErrorState error={entries.error} what="the Graveyard" />
        ) : (
          <Loading what="the Graveyard" />
        )}
      </>
    );
  }

  const allEntries = entries.data.items;
  const kinds = [...new Set(allEntries.map((row) => row.memory_type))].sort();
  const cycles = [...new Set(allEntries.map((row) => row.retirement_cycle))].sort((a, b) => a - b);
  const arms = orderArms(run.data?.arms ?? []);

  return (
    <>
      <h1>Graveyard</h1>
      <p className="lede">
        Every memory that left an active set, why it left, and what — if anything — still carries
        it. A memory marked <strong>compressed</strong> was folded into a summary the mind can still
        read; it has not been forgotten. Only <strong>evicted</strong> and{' '}
        <strong>superseded</strong> memories are genuinely out of reach.
      </p>

      <form
        className="controls"
        aria-label="Filter the Graveyard"
        onSubmit={(e) => {
          e.preventDefault();
        }}
      >
        <label>
          Mind
          <select
            value={arm}
            onChange={(e) => {
              setParam('arm', e.target.value);
            }}
            data-testid="filter-arm"
          >
            <option value="">All six</option>
            {arms.map((item) => (
              <option key={item.armId} value={item.armId}>
                {item.publicName}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select
            value={status}
            onChange={(e) => {
              setParam('status', e.target.value);
            }}
          >
            <option value="">Any</option>
            <option value="evicted">Evicted</option>
            <option value="compressed">Compressed</option>
            <option value="superseded">Superseded</option>
          </select>
        </label>
        <label>
          Memory kind
          <select
            value={kind}
            onChange={(e) => {
              setParam('kind', e.target.value);
            }}
          >
            <option value="">Any</option>
            {kinds.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Retirement cycle
          <select
            value={cycle}
            onChange={(e) => {
              setParam('cycle', e.target.value);
            }}
          >
            <option value="">Any</option>
            {cycles.map((value) => (
              <option key={value} value={String(value)}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Echo
          <select
            value={echoState}
            onChange={(e) => {
              setParam('echo', e.target.value);
            }}
          >
            <option value="">Any</option>
            <option value="with">Has a later resemblance</option>
            <option value="without">No resemblance measured</option>
          </select>
        </label>
        <label>
          Search text
          <input
            type="search"
            value={search}
            onChange={(e) => {
              setParam('q', e.target.value);
            }}
            placeholder="blue key"
            data-testid="filter-search"
          />
        </label>
        <label>
          Sort
          <select
            value={sort}
            onChange={(e) => {
              setParam('sort', e.target.value);
            }}
          >
            {SORTS.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </form>

      <p role="status" data-testid="graveyard-count">
        {filtered.length} of {allEntries.length} memories.
      </p>

      {filtered.length === 0 ? (
        <Empty>No memory matches those filters.</Empty>
      ) : (
        <ul className="entry-list">
          {filtered.map((row) => {
            const echo = echoByMemory.get(row.memory_id);
            return (
              <li
                className="entry"
                key={`${row.arm_id}:${row.memory_id}`}
                data-testid="graveyard-entry"
              >
                <header>
                  <h2>
                    <Link to={`/memory/${encodeURIComponent(row.memory_id)}`}>
                      {presentArm(row.arm_id).publicName}
                    </Link>{' '}
                    {statusTag(row.status)}
                  </h2>
                  <p className="meta mono">{row.memory_id}</p>
                </header>
                <blockquote>{row.text}</blockquote>
                <dl className="figures">
                  <div>
                    <dt>Born</dt>
                    <dd>cycle {row.birth_cycle}</dd>
                  </div>
                  <div>
                    <dt>Retired</dt>
                    <dd>
                      <Link to={`/cycle/${row.retirement_cycle}`}>
                        cycle {row.retirement_cycle}
                      </Link>
                    </dd>
                  </div>
                  <div>
                    <dt>Lifespan</dt>
                    <dd>{row.lifespan} cycles</dd>
                  </div>
                  <div>
                    <dt>Validated citations</dt>
                    <dd>{row.validated_citation_count}</dd>
                  </div>
                  <div>
                    <dt>Last cited</dt>
                    <dd>{row.last_cited_cycle ?? 'never'}</dd>
                  </div>
                  <div>
                    <dt>Kind</dt>
                    <dd>{row.memory_type}</dd>
                  </div>
                </dl>
                <p className="meta">
                  Reason: {row.retirement_reason.replaceAll('_', ' ')} · policy{' '}
                  <span className="mono">{row.policy_version}</span> · evidence{' '}
                  <span className="mono">{row.snapshot_evidence.slice(0, 22)}…</span>
                </p>
                {row.summary_descendant_id && (
                  <p>
                    Still carried by summary{' '}
                    <Link to={`/memory/${row.summary_descendant_id}`}>
                      {row.summary_descendant_id}
                    </Link>
                    .
                  </p>
                )}
                {echo && (
                  <p>
                    Nearest later resemblance at cycle {echo.cycle}, delta{' '}
                    <span className="mono">{echo.echo_delta.toFixed(3)}</span> —{' '}
                    {echo.category.replaceAll('_', ' ')}.
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

/**
 * One memory, from the cycle that made it to the cycle that lost it.
 *
 * Everything shown here is recorded. There is no prompt text and no evaluator
 * instruction on this page, and there is no route that would return one: the API
 * publishes prompt *versions* and *hashes*, which identify the apparatus without
 * handing it over.
 */

import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';

import { presentArm } from '../arms';
import { useApi } from '../context';
import { useOnce } from '../api/hooks';
import type { CycleView, EchoRow } from '../api/types';
import { Empty, ErrorState, Loading } from '../components/States';
import { statusTag } from './Graveyard';

export function MemoryDetail() {
  const api = useApi();
  const { memoryId = '' } = useParams();

  const entry = useOnce(() => api.graveyardEntry(memoryId).catch(() => null), [memoryId]);
  const lineage = useOnce(
    () => api.lineage(memoryId).catch(() => ({ parents: [], children: [] })),
    [memoryId],
  );
  const echoes = useOnce(() => api.echoes(), []);
  const graveyard = useOnce(() => api.graveyard({ limit: 200 }), []);
  const arms = useOnce(() => api.arms(), []);

  const record = entry.data ?? null;
  const armId = record?.arm_id ?? null;

  const deathCycle = useOnce<CycleView[]>(
    () => (record ? api.cycle(record.retirement_cycle) : Promise.resolve([])),
    [record?.retirement_cycle ?? 0],
  );

  const stillActive = useMemo(() => {
    for (const arm of arms.data ?? []) {
      const found = arm.active_memories.find((memory) => memory.memory_id === memoryId);
      if (found) return { arm: arm.arm_id, memory: found };
    }
    return null;
  }, [arms.data, memoryId]);

  const futureEchoes = useMemo(
    () =>
      (echoes.data?.items ?? []).filter(
        (row: EchoRow) => row.nearest_forgotten_memory_id === memoryId,
      ),
    [echoes.data, memoryId],
  );

  const textById = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of graveyard.data?.items ?? []) map.set(row.memory_id, row.text);
    for (const arm of arms.data ?? []) {
      for (const memory of arm.active_memories) map.set(memory.memory_id, memory.text);
    }
    return map;
  }, [graveyard.data, arms.data]);

  if (entry.status !== 'ready') {
    return (
      <>
        <h1>A memory</h1>
        <p className="meta mono">{memoryId}</p>
        {entry.status === 'error' ? (
          <ErrorState error={entry.error} what="this memory" />
        ) : (
          <Loading what="this memory" />
        )}
      </>
    );
  }

  const text = record?.text ?? stillActive?.memory.text ?? null;
  if (!text) {
    return (
      <>
        <h1>A memory</h1>
        <p className="meta mono">{memoryId}</p>
        <Empty>
          No memory with that identifier is recorded in this run.{' '}
          <Link to="/graveyard">Back to the Graveyard</Link>.
        </Empty>
      </>
    );
  }

  const owner = armId ?? stillActive?.arm ?? '';
  const snapshot = (deathCycle.data ?? []).find((view) => view.arm_id === owner) ?? null;

  return (
    <>
      <h1>A memory of {presentArm(owner).publicName}</h1>
      <p className="meta mono">{memoryId}</p>
      <blockquote className="lede">{text}</blockquote>

      <dl className="run-status">
        <div>
          <dt>Status</dt>
          <dd>{record ? statusTag(record.status) : <span className="tag">active</span>}</dd>
        </div>
        <div>
          <dt>Kind</dt>
          <dd>{record?.memory_type ?? stillActive?.memory.memory_kind ?? '—'}</dd>
        </div>
        <div>
          <dt>Born</dt>
          <dd>cycle {record?.birth_cycle ?? stillActive?.memory.birth_cycle ?? '—'}</dd>
        </div>
        <div>
          <dt>Retired</dt>
          <dd>
            {record ? (
              <Link to={`/cycle/${record.retirement_cycle}`}>cycle {record.retirement_cycle}</Link>
            ) : (
              'still active'
            )}
          </dd>
        </div>
        <div>
          <dt>Validated citations</dt>
          <dd>{record?.validated_citation_count ?? stillActive?.memory.citation_count ?? '—'}</dd>
        </div>
        <div>
          <dt>Last cited</dt>
          <dd>{record?.last_cited_cycle ?? '—'}</dd>
        </div>
      </dl>

      {record && (
        <section aria-labelledby="decision">
          <h2 id="decision">The decision that retired it</h2>
          <p>
            {record.retirement_reason.replaceAll('_', ' ')}, by policy{' '}
            <span className="mono">{record.policy_version}</span>. Evidence snapshot{' '}
            <span className="mono">{record.snapshot_evidence}</span>.
          </p>
          {record.summary_descendant_id && (
            <p>
              Its information was folded into summary{' '}
              <Link to={`/memory/${record.summary_descendant_id}`}>
                {record.summary_descendant_id}
              </Link>
              , which the mind can still read. This is compression, not loss.
            </p>
          )}
        </section>
      )}

      {snapshot && (
        <section aria-labelledby="context">
          <h2 id="context">What the mind held going into that cycle</h2>
          <p className="meta">
            Stimulus <span className="mono">{snapshot.stimulus_id}</span>: {snapshot.stimulus_text}
          </p>
          <p>
            It went from {snapshot.tokens_before} to {snapshot.tokens_after} tokens against a budget
            of {snapshot.budget_tokens}, and wrote:
          </p>
          <blockquote>{snapshot.candidate_memory}</blockquote>
          <p className="meta">
            Prompt versions{' '}
            {Object.entries(snapshot.prompt_versions)
              .map(([role, version]) => `${role} ${version}`)
              .join(', ')}
            . Prompt text is never published.
          </p>
        </section>
      )}

      <section aria-labelledby="lineage">
        <h2 id="lineage">Lineage</h2>
        {(lineage.data?.parents.length ?? 0) === 0 && (lineage.data?.children.length ?? 0) === 0 ? (
          <Empty>This memory neither absorbed another nor was absorbed into one.</Empty>
        ) : (
          <>
            {(lineage.data?.parents.length ?? 0) > 0 && (
              <>
                <h3>Compressed from</h3>
                <ul>
                  {lineage.data?.parents.map((id) => (
                    <li key={id}>
                      <Link to={`/memory/${encodeURIComponent(id)}`} className="mono">
                        {id}
                      </Link>{' '}
                      — {textById.get(id) ?? 'text not in this view'}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {(lineage.data?.children.length ?? 0) > 0 && (
              <>
                <h3>Absorbed into</h3>
                <ul>
                  {lineage.data?.children.map((id) => (
                    <li key={id}>
                      <Link to={`/memory/${encodeURIComponent(id)}`} className="mono">
                        {id}
                      </Link>{' '}
                      — {textById.get(id) ?? 'text not in this view'}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </section>

      <section aria-labelledby="echoes" data-testid="memory-echoes">
        <h2 id="echoes">Later resemblances</h2>
        {futureEchoes.length === 0 ? (
          <Empty>Nothing written later measured closer to this than to what the mind kept.</Empty>
        ) : (
          <ul className="entry-list">
            {futureEchoes.map((echo) => (
              <li className="entry" key={`${echo.cycle}:${echo.memory_id}`}>
                <p className="meta">
                  Cycle {echo.cycle} · {echo.category.replaceAll('_', ' ')} · delta{' '}
                  <span className="mono">{echo.echo_delta.toFixed(3)}</span> (forgotten{' '}
                  {echo.forgotten_similarity.toFixed(3)} − active{' '}
                  {echo.active_similarity.toFixed(3)})
                </p>
                <p>
                  A measured resemblance, not an access. Nothing here shows that the mind reached
                  this memory.
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

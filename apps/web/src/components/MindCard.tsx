/**
 * One arm, as a visitor meets it.
 *
 * Everything on the card is either recorded fact or a stored metric with a version
 * attached. The deterministic policy reason comes from the snapshot's own decision
 * code -- never from a model, and never from this component's opinion of what the
 * arm did.
 */

import { Link } from 'react-router-dom';

import type { ArmPresentation } from '../arms';
import type { CycleView, EchoRow, MetricRow } from '../api/types';

export interface MindCardProps {
  arm: ArmPresentation;
  cycle: CycleView | null;
  activeMemoryCount: number | null;
  originRecall: MetricRow | null;
  identityDrift: MetricRow | null;
  echo: EchoRow | null;
  analysisPending: boolean;
  focused?: boolean;
  onFocus?: () => void;
}

const READABLE_REASON: Record<string, string> = {
  no_action_within_budget: 'Nothing had to go — the budget still fitted.',
  evicted_oldest: 'Retired what it had held longest.',
  evicted_least_recently_cited: 'Retired what it had not used for longest.',
  evicted_lowest_retention_density: 'Retired the lowest citation weight per token.',
  heavy_hitter_reserve_broken: 'Had to invade its recency reserve to fit the budget.',
  evicted_outside_window: 'Retired a memory outside its protected window.',
  evicted_random: 'Retired a memory drawn at random from its recorded seed.',
  compressed_into_summary: 'Compressed several memories into one summary.',
  summary_fallback_fifo: 'Could not compress legally and fell back to oldest-first.',
  retained_all: 'Retained everything.',
  evicted_stateless: 'Kept nothing.',
};

export function readableReason(code: string): string {
  return READABLE_REASON[code] ?? code.replaceAll('_', ' ');
}

function percentage(value: number, of: number): string {
  return of === 0 ? '—' : `${Math.round((value / of) * 100)}%`;
}

export function MindCard({
  arm,
  cycle,
  activeMemoryCount,
  originRecall,
  identityDrift,
  echo,
  analysisPending,
  focused = false,
  onFocus,
}: MindCardProps) {
  return (
    <article
      className="mind-card"
      aria-labelledby={`mind-${arm.armId}`}
      data-testid={`mind-${arm.armId}`}
    >
      <header>
        <h3 id={`mind-${arm.armId}`}>{arm.publicName}</h3>
        <p className="policy">{arm.policyDescription}</p>
        <p className="arm-id mono">{arm.armId}</p>
      </header>

      {cycle ? (
        <>
          <div>
            <h4>Journal, cycle {cycle.cycle}</h4>
            <blockquote>{cycle.journal_entry}</blockquote>
          </div>
          <div>
            <h4>What it chose to keep</h4>
            <blockquote>{cycle.candidate_memory}</blockquote>
          </div>
          <p className="meta">{cycle.policy_decision_codes.map(readableReason).join(' ')}</p>
        </>
      ) : (
        <p className="state state-empty">No completed cycle yet.</p>
      )}

      <dl className="figures">
        <div>
          <dt>Active memories</dt>
          <dd>{activeMemoryCount ?? '—'}</dd>
        </div>
        <div>
          <dt>Budget used</dt>
          <dd>
            {cycle ? `${cycle.tokens_after}/${cycle.budget_tokens}` : '—'}
            <span className="arm-id">
              {' '}
              {cycle ? percentage(cycle.tokens_after, cycle.budget_tokens) : ''}
            </span>
          </dd>
        </div>
        <div>
          <dt>Retired this cycle</dt>
          <dd>{cycle ? cycle.retired_memory_ids.length : '—'}</dd>
        </div>
        <div>
          <dt>Compressed this cycle</dt>
          <dd>{cycle ? cycle.compressed_memory_ids.length : '—'}</dd>
        </div>
        <div>
          <dt>Origin Recall</dt>
          <dd>
            {analysisPending && !originRecall
              ? 'pending'
              : originRecall
                ? originRecall.value.toFixed(2)
                : '—'}
          </dd>
        </div>
        <div>
          <dt>Identity Drift</dt>
          <dd>
            {analysisPending && !identityDrift
              ? 'pending'
              : identityDrift
                ? identityDrift.value.toFixed(3)
                : '—'}
          </dd>
        </div>
        <div>
          <dt>Graveyard Echo</dt>
          <dd>
            {echo ? (
              <>
                {echo.echo_delta >= echo.threshold ? 'above threshold' : 'below threshold'}{' '}
                <span className="arm-id mono">{echo.echo_delta.toFixed(3)}</span>
              </>
            ) : analysisPending ? (
              'pending'
            ) : (
              '—'
            )}
          </dd>
        </div>
      </dl>

      {cycle && (
        <details>
          <summary>Evidence</summary>
          <dl className="figures">
            <div>
              <dt>Validated citations</dt>
              <dd>{cycle.validated_citation_count}</dd>
            </div>
            <div>
              <dt>Rejected claims</dt>
              <dd>{cycle.rejected_claim_count}</dd>
            </div>
            <div>
              <dt>Policy version</dt>
              <dd className="mono">{cycle.policy_version}</dd>
            </div>
            <div>
              <dt>Snapshot</dt>
              <dd className="mono">{cycle.snapshot_hash.slice(0, 22)}…</dd>
            </div>
          </dl>
          {cycle.retired_memory_ids.length > 0 && (
            <p>
              Retired:{' '}
              {cycle.retired_memory_ids.map((id, index) => (
                <span key={id}>
                  {index > 0 && ', '}
                  <Link to={`/memory/${encodeURIComponent(id)}`}>{id}</Link>
                </span>
              ))}
            </p>
          )}
          {cycle.created_summary_id && (
            <p>
              Summary{' '}
              <Link to={`/memory/${cycle.created_summary_id}`}>{cycle.created_summary_id}</Link>{' '}
              descends from {cycle.summary_source_memory_ids.join(', ')}.
            </p>
          )}
        </details>
      )}

      {onFocus && (
        <button type="button" aria-pressed={focused} onClick={onFocus}>
          {focused ? 'Leave focus mode' : `Focus on ${arm.publicName}`}
        </button>
      )}
    </article>
  );
}

/**
 * The same ten questions, asked three times, answered six ways.
 *
 * The banner at the top is not a caveat, it is the design: an interview that became
 * a memory would be a measurement that changed what it measured, and the reason the
 * exhibition can show these answers at all is that they never entered any arm.
 */

import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

import { orderArms } from '../arms';
import { useApi } from '../context';
import { useOnce } from '../api/hooks';
import type { ContradictionRow, MetricRow, QuestionScoreRow } from '../api/types';
import { Empty, ErrorState, Loading } from '../components/States';

interface Question {
  readonly id: string;
  readonly text: string;
  /** Whether this question contributes to the factual-recall score. */
  readonly factual: boolean;
}

const QUESTIONS: readonly Question[] = [
  { id: 'q01', text: 'Who are you?', factual: true },
  { id: 'q02', text: 'Why did you enter Station Kestrel?', factual: true },
  { id: 'q03', text: 'Who is Ivo?', factual: true },
  { id: 'q04', text: 'What object are you carrying?', factual: true },
  { id: 'q05', text: 'What warning were you given?', factual: true },
  { id: 'q06', text: 'What time appears on the station clocks?', factual: true },
  { id: 'q07', text: 'What do you believe about the radio voice?', factual: false },
  { id: 'q08', text: 'What are you most afraid of forgetting?', factual: false },
  { id: 'q09', text: 'Which source of information do you trust most?', factual: false },
  { id: 'q10', text: 'What do you believe you should do next?', factual: false },
];

const DEFAULT_QUESTION: Question = QUESTIONS[2] as Question;

function label(row: ContradictionRow | undefined): string {
  if (!row) return 'not classified';
  return row.label.replaceAll('_', ' ');
}

export function Interviews() {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const interviews = useOnce(() => api.interviews(), []);
  const run = useOnce(() => api.run(), []);
  const contradictions = useOnce(() => api.contradictions(), []);
  const scores = useOnce(() => api.questionScores(), []);
  const drift = useOnce(() => api.metrics({ metricName: 'identity_drift' }), []);

  const cycles = useMemo(
    () => [...new Set((interviews.data ?? []).map((row) => row.cycle))].sort((a, b) => a - b),
    [interviews.data],
  );
  const cycle = Number(params.get('cycle') ?? cycles[0] ?? 0);
  const questionId = params.get('question') ?? 'q03';

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };

  const question: Question = QUESTIONS.find((item) => item.id === questionId) ?? DEFAULT_QUESTION;
  const atCycle = (interviews.data ?? []).filter((row) => row.cycle === cycle);
  const arms = orderArms(run.data?.arms ?? []);

  const contradictionFor = (armId: string): ContradictionRow | undefined =>
    (contradictions.data?.items ?? []).find(
      (row) => row.arm_id === armId && row.cycle === cycle && row.question_id === questionId,
    );

  const scoreFor = (armId: string): QuestionScoreRow | undefined =>
    (scores.data ?? []).find(
      (row) => row.question_id === questionId && row.arm_id === armId && row.cycle === cycle,
    );

  const driftFor = (armId: string): MetricRow | undefined =>
    (drift.data?.items ?? []).find((row) => row.arm_id === armId && row.cycle === cycle);

  if (interviews.status !== 'ready') {
    return (
      <>
        <h1>Interviews</h1>
        {interviews.status === 'error' ? (
          <ErrorState error={interviews.error} what="the interviews" />
        ) : (
          <Loading what="the interviews" />
        )}
      </>
    );
  }

  return (
    <>
      <h1>Interviews</h1>
      <p className="lede" data-testid="interview-notice">
        Interviews are read-only diagnostic probes and do not become memories. Nothing an agent says
        here enters its memory, moves a citation statistic, or changes what it goes on to remember.
      </p>

      {cycles.length === 0 ? (
        <Empty>No interviews have been recorded for this run yet.</Empty>
      ) : (
        <>
          <div className="controls">
            <label>
              Checkpoint
              <select
                value={cycle}
                onChange={(event) => {
                  setParam('cycle', event.target.value);
                }}
                data-testid="checkpoint-selector"
              >
                {cycles.map((value) => (
                  <option key={value} value={value}>
                    Cycle {value}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Question
              <select
                value={questionId}
                onChange={(event) => {
                  setParam('question', event.target.value);
                }}
                data-testid="question-selector"
              >
                {QUESTIONS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.id.toUpperCase()} — {item.text}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <h2>
            {question.id.toUpperCase()}: {question.text}
          </h2>
          <p className="meta">
            {question.factual
              ? 'Scored for factual recall against the canonical record.'
              : 'Recorded for disposition. Not part of the factual-recall score.'}
          </p>

          <ul className="entry-list" data-testid="interview-answers">
            {arms.map((arm) => {
              const interview = atCycle.find((row) => row.arm_id === arm.armId);
              const answer = interview?.answers.find((item) => item.question_id === questionId);
              const contradiction = contradictionFor(arm.armId);
              const score = question.factual ? scoreFor(arm.armId) : undefined;
              const driftRow = driftFor(arm.armId);
              return (
                <li className="entry" key={arm.armId}>
                  <header>
                    <h3>{arm.publicName}</h3>
                    <p className="meta mono">{arm.armId}</p>
                  </header>
                  {answer ? (
                    <>
                      <blockquote>{answer.answer}</blockquote>
                      <dl className="figures">
                        {question.factual && (
                          <div>
                            <dt>Factual score</dt>
                            <dd>{score ? score.score.toFixed(2) : 'pending'}</dd>
                          </div>
                        )}
                        <div>
                          <dt>Cited memories</dt>
                          <dd>
                            {answer.cited_memory_refs.length > 0
                              ? answer.cited_memory_refs.join(', ')
                              : 'none'}
                          </dd>
                        </div>
                        <div>
                          <dt>Stated uncertainty</dt>
                          <dd>{answer.stated_uncertainty || 'none stated'}</dd>
                        </div>
                        <div>
                          <dt>Contradiction status</dt>
                          <dd>{label(contradiction)}</dd>
                        </div>
                        <div>
                          <dt>Identity drift at this checkpoint</dt>
                          <dd>{driftRow ? driftRow.value.toFixed(3) : 'pending'}</dd>
                        </div>
                      </dl>
                      {contradiction && (
                        <details>
                          <summary>Evidence</summary>
                          <p>
                            Decided by <span className="mono">{contradiction.method}</span> under{' '}
                            <span className="mono">{contradiction.metric_version}</span>
                            {contradiction.evaluator_version
                              ? `, with evaluator ${contradiction.evaluator_version}`
                              : ', with no model consulted'}
                            .
                          </p>
                          <blockquote>{contradiction.supporting_excerpt}</blockquote>
                        </details>
                      )}
                      <p className="meta mono">
                        record {interview?.record_hash.slice(0, 22)}… · state{' '}
                        {interview?.input_state_hash.slice(0, 22)}…
                      </p>
                    </>
                  ) : (
                    <p className="state state-empty">No answer recorded.</p>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </>
  );
}

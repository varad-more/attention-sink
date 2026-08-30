/**
 * Loading, empty, and error states, said in words rather than in a spinner.
 *
 * Every one of these is announced to a screen reader, and every error says what a
 * reader can actually do about it. "Something went wrong" is not a state.
 */

import type { ReactNode } from 'react';

import { ApiError } from '../api/client';

export function Loading({ what }: { what: string }) {
  return (
    <p className="state state-loading" role="status" aria-live="polite">
      Loading {what}…
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="state state-empty" role="status">
      {children}
    </p>
  );
}

export function ErrorState({ error, what }: { error: Error; what: string }) {
  const unreachable = error instanceof ApiError && error.status === 0;
  return (
    <div className="state state-error" role="alert">
      <p>
        <strong>Could not load {what}.</strong>
      </p>
      <p>{error.message}</p>
      {unreachable && (
        <p>
          The exhibition reads a local API. Start it with <code>make local-api</code>, and create a
          run with <code>make local-all</code> if you have not yet.
        </p>
      )}
    </div>
  );
}

export function AnalysisPending({ what }: { what: string }) {
  return (
    <p className="state state-pending" role="status">
      {what} has not been scored yet. Run <code>make local-analyze</code>.
    </p>
  );
}

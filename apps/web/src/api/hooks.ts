/**
 * Polling, and the rules that keep it from being rude or misleading.
 *
 * Polling rather than a socket: the pilot advances once every scheduler tick, and a
 * persistent connection for a value that changes every few seconds is a moving part
 * with nothing to do.
 *
 * Two behaviours matter more than the mechanism. A hidden tab does not poll -- a
 * background exhibition should cost nothing. And a *selected historical cycle* is
 * never re-fetched or overwritten: a reader who navigated to cycle 7 is reading
 * cycle 7, and a live update that moved them to cycle 12 would be a bug, not a
 * feature.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from './client';

export type AsyncState<T> =
  | { status: 'loading'; data: null; error: null }
  | { status: 'ready'; data: T; error: null }
  | { status: 'error'; data: null; error: ApiError | Error };

/** Fetch once. For immutable records and for anything a reader pinned deliberately. */
export function useOnce<T>(load: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading', data: null, error: null });

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: 'loading', data: null, error: null });
    load()
      .then((data) => {
        if (!controller.signal.aborted) setState({ status: 'ready', data, error: null });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setState({ status: 'error', data: null, error: error as Error });
        }
      });
    return () => {
      controller.abort();
    };
  }, deps);

  return state;
}

/** Whether the document is currently visible. Polling stops when it is not. */
function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(
    () => typeof document === 'undefined' || document.visibilityState !== 'hidden',
  );
  useEffect(() => {
    if (typeof document === 'undefined') return undefined;
    const onChange = () => {
      setVisible(document.visibilityState !== 'hidden');
    };
    document.addEventListener('visibilitychange', onChange);
    return () => {
      document.removeEventListener('visibilitychange', onChange);
    };
  }, []);
  return visible;
}

export interface PollOptions {
  /** Milliseconds between polls. */
  intervalMs: number;
  /** Stop polling once this returns true. Used to stop chasing finished analysis. */
  until?: (data: unknown) => boolean;
  /** Set false to freeze the value -- a reader is looking at something historical. */
  enabled?: boolean;
}

/**
 * Fetch, then keep fetching while the tab is visible and the work is unfinished.
 *
 * Returns the last good value through an error, so one failed poll does not blank a
 * view that was reading correctly a second ago. The error is surfaced beside the
 * data instead.
 */
export function usePolled<T>(
  load: () => Promise<T>,
  deps: readonly unknown[],
  options: PollOptions,
): AsyncState<T> & { stale: boolean; refresh: () => void } {
  const { intervalMs, until, enabled = true } = options;
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading', data: null, error: null });
  const [stale, setStale] = useState(false);
  const [nonce, setNonce] = useState(0);
  const visible = useDocumentVisible();
  const latest = useRef<T | null>(null);

  const refresh = useCallback(() => {
    setNonce((value) => value + 1);
  }, []);

  useEffect(() => {
    // An AbortController rather than a boolean: React's cleanup runs after `run` has
    // been written, so flow analysis narrows a plain local to `false` and calls every
    // check on it dead. `signal.aborted` is opaque to that, and is what this actually
    // means -- the effect was torn down, stop.
    const controller = new AbortController();
    const cancelled = () => controller.signal.aborted;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const run = async () => {
      try {
        const data = await load();
        if (cancelled()) return;
        latest.current = data;
        setStale(false);
        setState({ status: 'ready', data, error: null });
        if (until?.(data)) return;
      } catch (error) {
        if (cancelled()) return;
        if (latest.current !== null) {
          setStale(true);
          setState({ status: 'ready', data: latest.current, error: null });
        } else {
          setState({ status: 'error', data: null, error: error as Error });
        }
      }
      if (!cancelled() && enabled && visible) {
        timer = setTimeout(() => {
          void run();
        }, intervalMs);
      }
    };

    void run();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [...deps, intervalMs, enabled, visible, nonce]);

  return { ...state, stale, refresh };
}

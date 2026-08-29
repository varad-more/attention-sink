import { currentRuntimeMode, type RuntimeMode } from './runtime';

interface AppProps {
  /** Injected in tests; defaults to the build-time environment. */
  mode?: RuntimeMode;
}

/**
 * The application shell.
 *
 * Deliberately empty of experiment views: nothing can be rendered honestly until
 * there is a run to render. What it does carry is the simulated-data banner, which
 * has to exist from the first commit so that no later view can be added without it.
 */
export function App({ mode = currentRuntimeMode() }: AppProps) {
  const simulated = mode === 'local';

  return (
    <main>
      {simulated && (
        <div role="status" data-testid="simulated-banner">
          Simulated data. This client is running in local mode against fixtures; nothing shown here
          is a result of the canonical experiment.
        </div>
      )}
      <h1>Attention Sink</h1>
      <p>
        Six agents begin identical and diverge only in how they decide what to forget. No run has
        been published yet.
      </p>
    </main>
  );
}

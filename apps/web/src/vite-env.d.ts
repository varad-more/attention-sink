/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the read API. Required for any non-local build. */
  readonly VITE_API_BASE_URL?: string;
  /** The run this exhibition shows. Required for any non-local build. */
  readonly VITE_PUBLIC_RUN_ID?: string;
  /** `local`, `staging`, or `production`. Absent means local. */
  readonly VITE_DEPLOYMENT_MODE?: string;
  /** Milliseconds between polls of a live view. */
  readonly VITE_POLL_INTERVAL_MS?: string;
  /** Whether the data came from fixtures. Absent means yes outside production. */
  readonly VITE_FIXTURE_MODE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

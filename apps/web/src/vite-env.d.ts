/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Runtime mode injected at build time. Absent means local, which means simulated. */
  readonly VITE_RUNTIME_MODE?: string;
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/**
 * Which kind of data this client is showing.
 *
 * `local` means every figure on screen came from a fixture, not from a real run.
 * The distinction is load-bearing: a reader must never have to guess whether what
 * they are looking at actually happened, so the mode drives a permanent banner
 * rather than a console message.
 */
export type RuntimeMode = 'local' | 'production';

const KNOWN_MODES: readonly RuntimeMode[] = ['local', 'production'];

/**
 * Resolve the runtime mode from Vite's build-time environment.
 *
 * Defaults to `local` because an unconfigured client is, by definition, not
 * showing verified data. Failing towards "this is simulated" is the safe direction:
 * the opposite default would let fixture output pass as canonical.
 */
export function resolveRuntimeMode(raw: string | undefined): RuntimeMode {
  const candidate = (raw ?? '').trim().toLowerCase();
  return KNOWN_MODES.find((mode) => mode === candidate) ?? 'local';
}

export function currentRuntimeMode(): RuntimeMode {
  return resolveRuntimeMode(import.meta.env.VITE_RUNTIME_MODE);
}

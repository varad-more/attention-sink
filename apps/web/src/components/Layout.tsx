/**
 * The shell every page renders inside: landmarks, navigation, banner, and footer.
 *
 * The banner is not decoration and is not dismissible: a fixture build must say so on
 * every page rather than on the one a reader happened to enter through. The footer
 * carries the same duty for a build that is *not* a fixture, and reads its labels off
 * the run instead of asserting them, because only the run knows what produced it.
 */

import { NavLink, Outlet } from 'react-router-dom';

import { useOnce } from '../api/hooks';
import { useApi, useAppConfig } from '../context';

interface NavItem {
  readonly to: string;
  readonly label: string;
  /** True only for the index route, which would otherwise match every path. */
  readonly end: boolean;
}

const NAVIGATION: readonly NavItem[] = [
  { to: '/', label: 'Six Minds', end: true },
  { to: '/graveyard', label: 'Graveyard', end: false },
  { to: '/echoes', label: 'Echoes', end: false },
  { to: '/timeline', label: 'Timeline', end: false },
  { to: '/interviews', label: 'Interviews', end: false },
  { to: '/methodology', label: 'Methodology', end: false },
];

export function SimulatedBanner() {
  const config = useAppConfig();
  if (!config.fixtureMode) return null;
  return (
    <div className="banner" role="status" data-testid="simulated-banner">
      <strong>LOCAL SIMULATION</strong>
      <span>
        Every word below was produced by a deterministic local fixture, not by a language model,
        under an approximate token budget. Nothing here is evidence about how any model remembers.
      </span>
    </div>
  );
}

/** How each provenance label reads to a visitor who has not read the protocol. */
const LABEL_TEXT: Record<string, string> = {
  LOCAL_FIXTURE: 'Local fixture build',
  AWS_STAGING: 'Staging deployment',
  AWS_CANONICAL: 'Canonical run',
  CANONICAL: 'canonical',
  NON_CANONICAL: 'non-canonical',
  REAL_MODEL_OUTPUTS: 'real model outputs',
  SIMULATED_MODEL_OUTPUTS: 'simulated model outputs',
  EXACT_TOKEN_BUDGET: 'exact token budget',
  APPROXIMATE_TOKEN_BUDGET: 'approximate token budget',
};

/**
 * What produced the words on this page, read off the run rather than off the build.
 *
 * A hardcoded sentence here was wrong the moment the same code served a real run:
 * it told visitors that model output was a local fixture. The API already derives
 * these labels from the run's own configuration, so the footer states them and
 * invents nothing. While they are loading it says nothing rather than guessing.
 */
export function Provenance() {
  const api = useApi();
  const labels = useOnce(() => api.provenance(), []);
  const described = (labels.data ?? []).map((label) => LABEL_TEXT[label] ?? label);
  return (
    <p data-testid="provenance">
      A controlled experiment in application-level episodic memory.
      {described.length > 0 ? ` ${described.join(' — ')}.` : ''}
    </p>
  );
}

export function Layout() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <SimulatedBanner />
      <header className="site-header">
        <p className="wordmark">
          Attention&nbsp;Sink <span className="wordmark-sub">Six minds. One past. No room.</span>
        </p>
        <nav aria-label="Exhibition sections">
          <ul>
            {NAVIGATION.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) => (isActive ? 'nav-link is-active' : 'nav-link')}
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main id="main" tabIndex={-1}>
        <Outlet />
      </main>
      <footer className="site-footer">
        <Provenance />
      </footer>
    </>
  );
}

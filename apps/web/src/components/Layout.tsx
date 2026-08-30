/**
 * The shell every page renders inside: landmarks, navigation, and the banner.
 *
 * The banner is not decoration and is not dismissible. Everything this build shows
 * came from a deterministic fixture, and a reader must be told that on every page
 * rather than on the one they happened to enter through.
 */

import { NavLink, Outlet } from 'react-router-dom';

import { useAppConfig } from '../context';

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
        <p>
          A controlled experiment in application-level episodic memory. Local fixture build —
          non-canonical, not production research results.
        </p>
      </footer>
    </>
  );
}

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { App } from './App';
import { resolveRuntimeMode } from './runtime';

describe('App shell', () => {
  it('warns that data is simulated when running locally', () => {
    render(<App mode="local" />);
    expect(screen.getByTestId('simulated-banner')).toBeInTheDocument();
  });

  it('shows no simulation warning in production mode', () => {
    render(<App mode="production" />);
    expect(screen.queryByTestId('simulated-banner')).not.toBeInTheDocument();
  });
});

describe('resolveRuntimeMode', () => {
  it('accepts the two known modes', () => {
    expect(resolveRuntimeMode('production')).toBe('production');
    expect(resolveRuntimeMode('local')).toBe('local');
  });

  it('falls back to local for anything unrecognised, so nothing passes as canonical', () => {
    expect(resolveRuntimeMode(undefined)).toBe('local');
    expect(resolveRuntimeMode('')).toBe('local');
    expect(resolveRuntimeMode('prod')).toBe('local');
  });
});

import { describe, expect, it } from 'vitest';

import { ARM_PRESENTATION, orderArms, presentArm } from './arms';

describe('arm presentation', () => {
  it('names all six arms', () => {
    expect(ARM_PRESENTATION).toHaveLength(6);
    expect(ARM_PRESENTATION.map((arm) => arm.publicName)).toEqual([
      'Goldfish',
      'Present-Minded',
      'Pragmatist',
      'Keeper of the First Day',
      'Gambler',
      'Dreamer',
    ]);
  });

  it('keeps public names out of everything but presentation', () => {
    // ADR-004: the writer must never learn which mechanism it is. If a public name
    // appeared in an identifier, it would be one refactor from a prompt.
    for (const arm of ARM_PRESENTATION) {
      expect(arm.armId).toMatch(/^arm_[a-z]+$/);
      expect(arm.armId).not.toContain(arm.publicName.toLowerCase());
    }
  });

  it('falls back neutrally for an arm it does not name', () => {
    expect(presentArm('arm_unknown').publicName).toBe('arm_unknown');
  });

  it('orders arms as the protocol configures them, not alphabetically', () => {
    const ordered = orderArms(['arm_summary', 'arm_fifo', 'arm_sink']);
    expect(ordered.map((arm) => arm.armId)).toEqual(['arm_fifo', 'arm_sink', 'arm_summary']);
  });
});

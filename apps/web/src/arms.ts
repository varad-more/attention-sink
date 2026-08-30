/**
 * The public names, and the one place they are allowed to exist.
 *
 * ADR-004: a writer must never learn which mechanism it is. The names below are a
 * presentation mapping and nothing else -- they are not in the protocol, not in the
 * database, not in any API response, and not in any prompt. The API speaks
 * `arm_fifo`; only this file knows that a reader sees "Goldfish".
 *
 * The descriptions say what the mechanism does, in the terms a visitor can check
 * against the Graveyard. None of them says which arm is expected to do well.
 */

export interface ArmPresentation {
  readonly armId: string;
  readonly publicName: string;
  /** What the mechanism does, in one sentence a reader can verify. */
  readonly policyDescription: string;
  /** A longer note for the focus view and the methodology page. */
  readonly detail: string;
}

export const ARM_PRESENTATION: readonly ArmPresentation[] = [
  {
    armId: 'arm_fifo',
    publicName: 'Goldfish',
    policyDescription: 'Forgets whatever it has held longest.',
    detail:
      'When the budget binds, the oldest memory goes, whatever it says and however ' +
      'often it has been used. The simplest possible rule, and the baseline every ' +
      'other mechanism has to beat.',
  },
  {
    armId: 'arm_lru',
    publicName: 'Present-Minded',
    policyDescription: 'Forgets whatever it has not used for longest.',
    detail:
      'Retires the memory whose last verified citation is furthest in the past. A ' +
      'memory it keeps referring to survives; one it has stopped needing does not.',
  },
  {
    armId: 'arm_heavy',
    publicName: 'Pragmatist',
    policyDescription: 'Keeps what it has cited most, per token it costs.',
    detail:
      'Scores each memory by discounted citation weight against its token cost and ' +
      'retires the lowest, while reserving space for the most recent arrivals so a ' +
      'new memory is never evicted before it has had a chance to be used.',
  },
  {
    armId: 'arm_sink',
    publicName: 'Keeper of the First Day',
    policyDescription: 'Protects one founding memory and forgets around it.',
    detail:
      'One seed -- the name -- can never be retired. Everything else is evicted ' +
      'oldest-first around it. The arm that cannot forget who it is.',
  },
  {
    armId: 'arm_random',
    publicName: 'Gambler',
    policyDescription: 'Forgets at random, from a recorded seed.',
    detail:
      'Chooses uniformly among eligible memories using a seed stored in the ' +
      'protocol, so the same run replays exactly. The control that shows how much ' +
      'of any other arm’s result is the mechanism and how much is chance.',
  },
  {
    armId: 'arm_summary',
    publicName: 'Dreamer',
    policyDescription: 'Compresses several memories into one, and keeps the summary.',
    detail:
      'When the budget binds it plans a compression, spends an extra model call to ' +
      'write the summary, and charges that summary against the same budget as any ' +
      'other memory. Its sources are marked compressed rather than evicted, because ' +
      'the summary still carries them.',
  },
];

const BY_ID = new Map(ARM_PRESENTATION.map((arm) => [arm.armId, arm]));

/** The presentation for one arm, or a neutral fallback for an arm we do not name. */
export function presentArm(armId: string): ArmPresentation {
  return (
    BY_ID.get(armId) ?? {
      armId,
      publicName: armId,
      policyDescription: 'An unnamed mechanism.',
      detail: 'This build has no presentation mapping for this arm.',
    }
  );
}

/** Arms in exhibition order, which is the protocol's configured order. */
export function orderArms(armIds: readonly string[]): ArmPresentation[] {
  const known = ARM_PRESENTATION.filter((arm) => armIds.includes(arm.armId));
  const unknown = armIds.filter((id) => !BY_ID.has(id)).map(presentArm);
  return [...known, ...unknown];
}

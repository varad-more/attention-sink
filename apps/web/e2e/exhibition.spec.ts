/**
 * The fourteen flows a visitor actually takes.
 *
 * Each one is a thing a person does, not a component rendered in isolation. They run
 * against the real local API, so the assertion "the Graveyard shows what the Dreamer
 * compressed" is an assertion about the whole system.
 */

import { expect, test, type Page } from '@playwright/test';

const MOBILE = 'mobile';

/**
 * Whether these flows are running against a deployed site rather than a local build.
 *
 * One assertion depends on it, and it is the one that matters most: a local build
 * runs on fixtures and must say so, and a deployed canonical run must not, because
 * every word on it came from a real model.
 */
const DEPLOYED = Boolean(process.env.E2E_BASE_URL?.trim());

async function firstGraveyardEntry(page: Page) {
  await page.goto('/graveyard');
  await expect(page.getByTestId('graveyard-count')).toBeVisible();
  return page.getByTestId('graveyard-entry').first();
}

test('1-2: the landing page opens and labels what produced it', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Six minds');
  const banner = page.getByTestId('simulated-banner');
  if (DEPLOYED) {
    await expect(banner).toHaveCount(0);
  } else {
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('LOCAL SIMULATION');
  }
  await expect(page.getByTestId('current-cycle')).toContainText('of 24');
});

test('1-2b: the footer states what produced the words, from the run itself', async ({ page }) => {
  await page.goto('/');
  const provenance = page.getByTestId('provenance');
  await expect(provenance).toContainText('application-level episodic memory');
  await expect(provenance).toContainText(
    DEPLOYED ? 'real model outputs' : 'simulated model outputs',
  );
});

test('3: all six minds can be compared at once', async ({ page }) => {
  await page.goto('/');
  for (const name of [
    'Goldfish',
    'Present-Minded',
    'Pragmatist',
    'Keeper of the First Day',
    'Gambler',
    'Dreamer',
  ]) {
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  }
  const table = page.getByTestId('comparison-table');
  await expect(table).toBeVisible();
  await expect(table.locator('tbody tr')).toHaveCount(6);
});

test('4: a historical cycle can be selected and stays selected', async ({ page }) => {
  await page.goto('/cycle/7');
  await expect(page.getByTestId('current-cycle')).toContainText('7 of 24');
  await expect(page.getByText('which is frozen')).toBeVisible();
  await page.waitForTimeout(1200);
  await expect(page.getByTestId('current-cycle')).toContainText('7 of 24');
});

test('5-6: a forgotten memory opens and shows why it was retired', async ({ page }) => {
  const entry = await firstGraveyardEntry(page);
  await expect(entry).toBeVisible();
  await entry.getByRole('link').first().click();
  await expect(page.getByRole('heading', { name: /A memory of/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'The decision that retired it' })).toBeVisible();
  await expect(page.getByText(/policy/)).toBeVisible();
});

test('7: an echo comparison shows both texts and the delta', async ({ page }) => {
  await page.goto('/echoes');
  await expect(page.getByRole('heading', { name: 'Graveyard Echo' })).toBeVisible();
  await expect(page.getByText('measured distance and not an access')).toBeVisible();
  const first = page.getByTestId('echo-list').locator('li').first();
  await expect(first.getByRole('heading', { name: 'What it had lost' })).toBeVisible();
  await expect(first.getByRole('heading', { name: 'What it wrote later' })).toBeVisible();
  await expect(first.getByText('Echo delta')).toBeVisible();
});

test('8: Q03 can be compared across every checkpoint', async ({ page }) => {
  await page.goto('/interviews?question=q03');
  await expect(page.getByTestId('interview-notice')).toContainText('do not become memories');
  for (const cycle of ['0', '12', '24']) {
    await page.getByTestId('checkpoint-selector').selectOption(cycle);
    await expect(page.getByRole('heading', { name: /^Q03/ })).toBeVisible();
    await expect(page.getByTestId('interview-answers').locator('> li')).toHaveCount(6);
  }
});

test('9: the timeline scrubs and the table follows', async ({ page }) => {
  await page.goto('/timeline');
  await expect(page.getByRole('img', { name: /Activity for six minds/ })).toBeVisible();
  const scrubber = page.getByTestId('timeline-scrubber');
  await scrubber.fill('12');
  await expect(page.getByRole('heading', { name: 'Cycle 12, in figures' })).toBeVisible();
  await expect(page.getByTestId('timeline-table').locator('tbody tr')).toHaveCount(6);
});

test('10: the Dreamer compression lineage resolves both ways', async ({ page }) => {
  await page.goto('/graveyard?arm=arm_summary&status=compressed');
  const entry = page.getByTestId('graveyard-entry').first();
  await expect(entry.getByText('Still carried by summary')).toBeVisible();
  await entry.getByText('Still carried by summary').getByRole('link').click();
  await expect(page.getByRole('heading', { name: 'Lineage' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Compressed from' })).toBeVisible();
});

test('11b: methodology names what produced the words, and does not misname it', async ({
  page,
}) => {
  await page.goto('/methodology');
  const limitations = page.getByTestId('limitations');
  await expect(limitations).toContainText(
    DEPLOYED ? 'One model, one setting, one repetition' : 'Fixture output is not evidence',
  );
  if (DEPLOYED) {
    await expect(limitations).not.toContainText('This build runs deterministic local fixtures');
  }
});

test('11: methodology states the limitations', async ({ page }) => {
  await page.goto('/methodology');
  await expect(page.getByRole('heading', { name: 'Methodology' })).toBeVisible();
  const limitations = page.getByTestId('limitations');
  await expect(limitations).toContainText('not an internal KV cache');
  await expect(limitations).toContainText('not evidence about a production model');
  await expect(limitations).toContainText('are not conscious');
});

test('12: export metadata is visible with its provenance labels', async ({ page }) => {
  await page.goto('/methodology#export');
  const table = page.getByTestId('export-table');
  await expect(table).toBeVisible();
  await expect(table).toContainText('LOCAL_FIXTURE');
  await expect(table).toContainText('NON_CANONICAL');
});

test('13: the core flow works on a small screen', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== MOBILE, 'the mobile project covers this');
  await page.goto('/');
  await expect(page.getByTestId('simulated-banner')).toBeVisible();
  await expect(page.getByTestId('mind-arm_fifo')).toBeVisible();
  // Scoped to the navigation landmark: the landing page also links to the Graveyard,
  // and a bare name would match both.
  await page
    .getByRole('navigation', { name: 'Exhibition sections' })
    .getByRole('link', { name: 'Graveyard' })
    .click();
  await expect(page.getByRole('heading', { name: 'Graveyard' })).toBeVisible();
  await expect(page.getByTestId('graveyard-entry').first()).toBeVisible();
});

test('14: primary navigation is reachable by keyboard alone', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === MOBILE, 'keyboard navigation is a desktop flow');
  await page.goto('/');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused();

  for (const name of ['Graveyard', 'Timeline', 'Interviews', 'Methodology']) {
    await page.goto('/');
    const link = page
      .getByRole('navigation', { name: 'Exhibition sections' })
      .getByRole('link', { name, exact: true });
    await link.focus();
    await expect(link).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { level: 1 })).toContainText(
      name === 'Timeline' ? 'Timeline' : name === 'Interviews' ? 'Interviews' : name,
    );
  }
});

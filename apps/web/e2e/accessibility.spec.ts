/**
 * Accessibility checks that do not need a third-party auditor to be worth running.
 *
 * Landmarks, one h1 per page, a focusable skip link, labelled controls, and a text
 * alternative for every visualisation. These are the failures that actually make an
 * exhibition unusable, and they are cheap to assert directly.
 */

import { expect, test } from '@playwright/test';

const PAGES = ['/', '/graveyard', '/echoes', '/timeline', '/interviews', '/methodology'];

for (const path of PAGES) {
  test(`${path} has one main landmark and exactly one level-one heading`, async ({ page }) => {
    await page.goto(path);
    await expect(page.locator('main')).toHaveCount(1);
    await expect(page.locator('header')).not.toHaveCount(0);
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
  });

  test(`${path} offers a skip link as its first stop`, async ({ page }) => {
    await page.goto(path);
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused();
  });

  test(`${path} labels every form control`, async ({ page }) => {
    await page.goto(path);
    const controls = page.locator('select, input');
    for (let index = 0; index < (await controls.count()); index += 1) {
      const control = controls.nth(index);
      const labelled =
        (await control.getAttribute('aria-label')) ??
        (await control.evaluate((node) => node.closest('label') !== null));
      expect(labelled, `control ${index} on ${path} has no label`).toBeTruthy();
    }
  });
}

test('the timeline chart carries a text alternative and a table', async ({ page }) => {
  await page.goto('/timeline');
  await expect(page.getByRole('img', { name: /Activity for six minds/ })).toBeVisible();
  await expect(page.locator('figcaption')).not.toHaveCount(0);
  await expect(page.getByTestId('timeline-table')).toBeVisible();
});

test('status is never carried by colour alone', async ({ page }) => {
  await page.goto('/graveyard');
  const tag = page.locator('.tag').first();
  await expect(tag).toHaveText(/evicted|compressed|superseded/);
});

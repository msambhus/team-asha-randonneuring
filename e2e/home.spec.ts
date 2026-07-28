import { test, expect } from '@playwright/test';

test('home page renders', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveTitle(/Team Asha/);
});

test('private My Rides requires authentication', async ({ page }) => {
  await page.goto('/auth/my-rides');
  await expect(page).toHaveURL(/\/auth\/login/);
  await expect(page.getByText(/please log in to view your rides/i)).toBeVisible();
});

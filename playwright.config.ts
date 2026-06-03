import { defineConfig, devices } from '@playwright/test';

const baseURL =
  process.env.PLAYWRIGHT_BASE_URL || process.env.BASE_URL || 'http://localhost:5000';

export default defineConfig({
  testDir: './e2e',
  use: { baseURL, trace: 'on-first-retry' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'python3 app.py',
    url: baseURL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});

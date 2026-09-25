import { defineConfig, devices } from '@playwright/test'

/**
 * Four projects, because every page has to be right in four places: a phone and
 * a desktop, each in light and dark. Every spec runs in all four rather than
 * picking one and hoping — dark mode and 375px are exactly where this app broke
 * before, and a check that only runs at 1280 light would have caught none of it.
 *
 * `colorScheme` is set for honesty rather than effect: the app defaults to
 * light regardless of the OS now, so the theme is forced through localStorage
 * in tests/support/app.ts. Setting the emulated scheme too means a bug where
 * something still reads `prefers-color-scheme` shows up as a mismatch.
 *
 * The dev server is started for us and reused if one is already up, so a single
 * `python tasks.py ui-check` needs nothing running first — and needs no
 * database either, because the API is replayed from fixtures.
 */
export default defineConfig({
  testDir: './tests',
  outputDir: './test-results',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],

  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'mobile-light',
      use: { ...devices['Pixel 7'], viewport: { width: 375, height: 812 }, colorScheme: 'light' },
    },
    {
      name: 'mobile-dark',
      use: { ...devices['Pixel 7'], viewport: { width: 375, height: 812 }, colorScheme: 'dark' },
    },
    {
      name: 'desktop-light',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 }, colorScheme: 'light' },
    },
    {
      name: 'desktop-dark',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 }, colorScheme: 'dark' },
    },
  ],

  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})

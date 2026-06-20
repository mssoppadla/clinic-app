// Real-user browser e2e config. Points at the running stack (Caddy on :8085 locally, the prod
// host in CI/prod). Fails the run on ANY uncaught page error (wired per-test) so a broken page
// is caught the way a user would experience it.
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: '.',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:8085',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});

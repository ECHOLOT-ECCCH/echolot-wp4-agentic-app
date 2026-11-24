import { defineConfig, devices } from '@playwright/test'

import { E2E_USER } from './e2e/account'

const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:5173'
const apiURL = process.env.E2E_API_URL ?? 'http://127.0.0.1:8000'
const ci = Boolean(process.env.CI)

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: ci ? 1 : 0,
  reporter: ci ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.E2E_NO_SERVERS
    ? undefined
    : [
        {
          // the test model answers without a key and calls every tool it sees; the e2e account
          // is created first (on a reused data directory it is already there, which is fine)
          command:
            `echo '${E2E_USER.password}' | uv run echolot-admin create-user ${E2E_USER.email} ` +
            `--admin --password-stdin; ` +
            'uv run uvicorn echolot_app.main:app --host 127.0.0.1 --port 8000',
          cwd: '../..',
          env: {
            ECHO_DATA_DIR: './data/e2e',
            ECHO_MODEL_PROVIDER: 'test',
            ECHO_MODEL_ID: 'test',
            ECHO_ENABLE_TEST_MODEL: '1',
          },
          url: `${apiURL}/healthz`,
          reuseExistingServer: !ci,
          timeout: 60_000,
        },
        {
          command: 'pnpm exec vite --port 5173 --strictPort',
          url: baseURL,
          reuseExistingServer: !ci,
          timeout: 60_000,
        },
      ],
})

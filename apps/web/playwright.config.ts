import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:5173'
const apiURL = process.env.E2E_API_URL ?? 'http://localhost:8000'
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
  projects: [
    { name: 'setup', testMatch: /global\.setup\.ts/ },
    {
      name: 'chromium',
      dependencies: ['setup'],
      use: { ...devices['Desktop Chrome'], storageState: 'e2e/.auth/state.json' },
    },
  ],
  webServer: process.env.E2E_NO_SERVERS
    ? undefined
    : [
        {
          command: 'uv run uvicorn elexifier_api.main:app --port 8000',
          cwd: '../..',
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

// Step-by-step usage screenshots (screenshots/README.md), taken against a running app with a
// real model and the local executor, on an empty data directory:
//
//   echo 'shots-password' | ECHO_DATA_DIR=/tmp/shots uv run echolot-admin create-user \
//     demo@example.org --name Demo --admin --password-stdin
//   ECHO_DATA_DIR=/tmp/shots make api      # .env with a model key; services.toml, runner.env
//   make web
//   cd apps/web && node scripts/usage-screenshots.mjs ../../screenshots ../../demo/people-demo.csv
//
// SHOTS_EMAIL and SHOTS_PASSWORD override the account above.
//
// The agent's words differ from run to run; steps 14 and 15 assume demo/people-demo.csv.
import { chromium } from '@playwright/test'

const OUT = process.argv[2]
const CSV = process.argv[3]
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
const shot = async (name) => {
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log('saved', name)
}
const stop = () => page.getByRole('button', { name: 'Stop generating' })
const allow = () => page.getByRole('button', { name: 'Allow', exact: true })
async function waitTurn() {
  await stop()
    .waitFor({ timeout: 30_000 })
    .catch(() => {})
  await stop().waitFor({ state: 'detached', timeout: 600_000 })
  await page.waitForTimeout(1500)
}
async function say(text) {
  const composer = page.getByPlaceholder('Send a message...')
  await composer.fill(text)
  await composer.press('Enter')
  await waitTurn()
}

// sign in
await page.goto('http://localhost:5173/')
await page.waitForURL(/\/login/)
await page.getByLabel('E-mail').fill(process.env.SHOTS_EMAIL ?? 'demo@example.org')
await page.getByLabel('Password').fill(process.env.SHOTS_PASSWORD ?? 'shots-password')
await page.getByRole('button', { name: 'Sign in' }).click()
await page.waitForURL((url) => !url.pathname.startsWith('/login'))

// 1-3: workspaces, create one, the empty workspace
await page.getByRole('heading', { name: 'ECHOLOT Workbench' }).waitFor()
await shot('01-workspaces')
await page.getByRole('button', { name: /new workspace/i }).click()
await page.getByLabel('Name').fill('ehkultura people → Wikidata')
await page
  .getByLabel(/Description/)
  .fill(
    'People from our catalogue, to be linked to Wikidata items through the ECHOLOT reconciliation service.',
  )
await shot('02-new-workspace')
await page.getByRole('button', { name: 'Create' }).click()
await page.getByRole('link', { name: /ehkultura people/ }).click()
await page.getByText('Nothing selected').waitFor()
await shot('03-empty-workspace')

// 4: upload and browse the records
await page.locator('input[type=file]').setInputFiles(CSV)
await page.getByText('20 records').first().waitFor()
await page.getByRole('button', { name: /^people-demo\.csv/ }).click()
await page.getByText('rows 0–19 of 20').waitFor()
await shot('04-records')

// 5-7: ask the agent; it looks at the data, proposes, writes task notes
await say(
  "I uploaded a small list of people from our catalogue. I'd like to link each of them to Wikidata. Can you have a look at the file first?",
)
await shot('05-agent-looks-at-the-data')
await page
  .getByText(/\d+ tool calls/)
  .first()
  .click()
await shot('06-tool-calls')
await page
  .getByText(/\d+ tool calls/)
  .first()
  .click()
await page.getByRole('button', { name: 'Task notes', exact: true }).click()
await shot('07-task-notes')

// 8-10: agree, the agent writes the script and asks to run a sample
await say(
  "Yes, use the description as context, and flag matches whose title doesn't match the label. Let's start with a sample of 10.",
)
if (!(await allow().first().isVisible())) {
  await say('Please write the script and run it on a sample of 10.')
}
await allow().first().scrollIntoViewIfNeeded()
await shot('08-approval-card')
await allow().first().click()
await waitTurn()
await shot('09-sample-report')
await page.locator('button', { hasText: 'sample of 10' }).first().click()
await page.getByTestId('run-counts').waitFor()
await shot('10-sample-run')

// 11-13: the script, then Run all from the editor
await page.getByRole('button', { name: /^Script/ }).click()
await page.getByLabel('Script code').waitFor()
await shot('11-script')
await page.getByRole('button', { name: 'Run all', exact: true }).click()
// wait for the progress bar to move, so the picture shows a running run
for (let i = 0; i < 200; i++) {
  const progress = await page
    .getByText(/^\d+ \/ 20$/)
    .first()
    .textContent()
    .catch(() => null)
  if (progress && !progress.startsWith('0 ')) break
  await page.waitForTimeout(100)
}
await page.screenshot({ path: `${OUT}/12-run-all-in-progress.png` })
console.log('saved 12-run-all-in-progress')
await page.getByTestId('run-counts').waitFor({ timeout: 120_000 })
await page.getByTestId('candidates').first().waitFor()
await page.getByText('result-', { exact: false }).nth(1).waitFor() // both result files listed
await shot('13-run-results')

// 14-15: review: decide per row, then filter
await page.getByLabel('Decision for row 0').selectOption('Q220980')
await page.getByLabel('Decision for row 19').selectOption('__none__')
await page.getByLabel('Decision for row 16').selectOption('__none__')
await page.waitForTimeout(800)
await shot('14-decisions')
await page.getByLabel('Show results').selectOption('verdict:conflict')
await page.waitForTimeout(1000)
await shot('15-filter-conflicts')

console.log('page errors:', JSON.stringify(errors))
await browser.close()

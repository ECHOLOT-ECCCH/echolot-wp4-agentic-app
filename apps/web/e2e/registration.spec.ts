import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

import { E2E_USER } from './account'

// the e2e API writes its mail here (playwright.config.ts: ECHO_MAIL_BACKEND=file)
const OUTBOX = path.join(path.dirname(fileURLToPath(import.meta.url)), '../../../data/e2e/outbox')
const PASSWORD = 'maja-password-not-secret'

/** The body of the newest message to this address, quoted-printable decoded. */
async function mailTo(address: string): Promise<string | null> {
  let names: string[]
  try {
    names = (await readdir(OUTBOX)).filter((name) => name.endsWith('.eml')).sort()
  } catch {
    return null
  }
  for (const name of names.reverse()) {
    const raw = await readFile(path.join(OUTBOX, name), 'utf8')
    if (!raw.includes(`To: ${address}`)) continue
    return raw
      .replace(/=\r?\n/g, '')
      .replace(/=([0-9A-F]{2})/g, (_, hex: string) => String.fromCharCode(parseInt(hex, 16)))
  }
  return null
}

async function signIn(page: Page, email: string, password: string) {
  await page.goto('/login')
  await page.getByLabel('E-mail').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

test('a person asks for an account, confirms the address, an admin approves', async ({
  page,
  browser,
}) => {
  const email = `maja-${Date.now()}@example.org`

  await page.goto('/login')
  await page.getByRole('link', { name: 'Request an account' }).click()
  await page.waitForURL(/\/register/)
  await page.getByLabel('Name').fill('Maja Novak')
  await page.getByLabel('Organisation').fill('Example Institute')
  await page.getByLabel('E-mail').fill(email)
  await page.getByLabel('What will you use the workbench for?').fill('Reconcile our archive.')
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByLabel('Password again').fill(PASSWORD)
  await page.getByRole('button', { name: 'Request an account' }).click()
  await expect(page.getByText('Check your inbox')).toBeVisible()

  let body: string | null = null
  await expect.poll(async () => (body = await mailTo(email)), { timeout: 10_000 }).not.toBeNull()
  const link = /(http\S+\/confirm-email\?token=\S+)/.exec(body!)?.[1]
  expect(link).toBeTruthy()

  // before confirming, the right password is told what is missing
  await signIn(page, email, PASSWORD)
  await expect(page.getByRole('alert')).toContainText('Confirm your e-mail address')

  await page.goto(link!)
  await page.getByRole('button', { name: 'Confirm my address' }).click()
  await expect(page.getByText('Your address is confirmed')).toBeVisible()

  await signIn(page, email, PASSWORD)
  await expect(page.getByRole('alert')).toContainText('waiting for approval')

  // the admin approves it under Settings → Users, where the e-mail to admins links
  const admin = await browser.newPage()
  await signIn(admin, E2E_USER.email, E2E_USER.password)
  await admin.waitForURL((url) => !url.pathname.startsWith('/login'))
  await admin.goto('/settings?tab=users')
  const requests = admin.locator('section', { hasText: 'Requests for an account' })
  const card = requests.locator('div.rounded-lg', { hasText: email })
  await expect(card).toContainText('Reconcile our archive.')
  await card.getByRole('button', { name: 'Approve' }).click()
  await expect(admin.getByText('Maja Novak can sign in now')).toBeVisible()
  await expect(requests.getByText(email)).toHaveCount(0)
  await admin.close()

  await expect.poll(async () => (await mailTo(email)) ?? '').toContain('is approved')
  await signIn(page, email, PASSWORD)
  await page.waitForURL((url) => !url.pathname.startsWith('/login'))
  await expect(page.getByRole('heading', { name: 'ECHOLOT Workbench' })).toBeVisible()
})

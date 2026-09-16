import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

const HERE = path.dirname(fileURLToPath(import.meta.url))

const SAMPLES = path.resolve(HERE, '../../../packages/pipeline/src/elexifier_pipeline/sample')
const stamp = () => new Date().toISOString().replace(/\D/g, '').slice(0, 14)

async function createProject(page: Page, name: string) {
  await page.goto('/')
  await page.getByRole('heading', { name: 'Elexifier' }).waitFor()
  await page.getByRole('button', { name: /new project/i }).click()
  await page.getByLabel('Name').fill(name)
  await page.getByRole('button', { name: 'Create' }).click()
  await page.getByRole('link', { name: new RegExp(name) }).click()
  await page.waitForURL(/\/projects\//)
  await page.getByText('Nothing selected').waitFor()
}

async function ensureTestCredential(page: Page, label: string) {
  await page.goto('/settings/credentials')
  await page.getByRole('heading', { name: 'Model credentials' }).waitFor()
  await page.selectOption('#cred-provider', 'test')
  await page.getByLabel('Label').fill(label)
  await page.getByRole('button', { name: 'Save credential' }).click()
  const card = page.locator('[data-slot=card]', { hasText: label })
  await card.waitFor()
  await card.getByRole('button', { name: 'Test' }).click()
  await expect(card.getByText(/tokens\)/)).toBeVisible()
}

test.describe('workspace smoke', () => {
  test('upload, preview, choose a model, chat with a tool call, approve, reload', async ({
    page,
  }) => {
    const id = stamp()
    await ensureTestCredential(page, `Test model ${id}`)
    await createProject(page, `Smoke ${id}`)

    // upload an XML and a PDF, both appear in the file list
    await page
      .locator('input[type=file]')
      .setInputFiles([
        path.join(SAMPLES, 'sample-dictionary.xml'),
        path.join(SAMPLES, 'sample-dictionary.pdf'),
      ])
    const xmlButton = page.getByRole('button', { name: /^sample-dictionary\.xml \d+ B$/ })
    const pdfButton = page.getByRole('button', { name: /^sample-dictionary\.pdf [\d.]+ KB$/ })
    await expect(xmlButton).toBeVisible()
    await expect(pdfButton).toBeVisible()

    // the XML upload is analysed by the worker: the document appears and becomes browsable
    const document = page.getByRole('button', { name: /^sample-dictionary\.xml.*entries$/ })
    await expect(document, 'document did not become ready; is `make worker` running?').toBeVisible({
      timeout: 90_000,
    })
    await document.click()
    await expect(page.locator('p', { hasText: /^2 entries$/ })).toBeVisible()
    await page.getByRole('button', { name: /hiša/ }).click()
    await expect(page.locator('.cm-editor')).toBeVisible()
    await expect(page.locator('.cm-content')).toContainText('stavba, v kateri ljudje prebivajo')
    await page.getByLabel('Search entries').fill('brati')
    await page.getByRole('button', { name: /brati/ }).click()
    await expect(page.locator('.cm-content')).toContainText('razpoznavati znake pisave')

    // clicking the analysed file opens the same entry browser
    await xmlButton.click()
    await expect(page.locator('p', { hasText: /^2 entries$/ })).toBeVisible()

    // PDF preview renders a page and the page indicator
    await pdfButton.click()
    await expect(page.locator('.react-pdf__Page').first()).toBeVisible()
    await expect(page.getByText('1 / 2')).toBeVisible()

    // pick the test model for the project
    await page.getByRole('button', { name: /choose a model/i }).click()
    await page.getByRole('button', { name: `Test model ${id}` }).click()
    await page
      .getByRole('button', { name: /Test model \(canned answers\)/ })
      .first()
      .click()
    await expect(page.getByRole('button', { name: /^test$/i })).toBeVisible()

    // chat: the test model calls the tools it sees; the gated demo tool asks for approval
    const composer = page.getByPlaceholder(/send a message/i)
    await composer.fill('What files do I have?')
    await composer.press('Enter')
    await expect(page.getByRole('button', { name: 'Allow', exact: true })).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.getByText('Listing project files')).toBeVisible()
    await page.getByText('Listing project files').click()
    await expect(page.getByText(/2 file\(s\) in project/).first()).toBeVisible()

    await page.getByRole('button', { name: 'Allow', exact: true }).click()
    await expect(page.getByText(/Approved and performed/).first()).toBeVisible({ timeout: 30_000 })

    // the conversation survives a reload
    await page.reload()
    await expect(
      page.locator('[data-role="user"]').getByText('What files do I have?'),
    ).toBeVisible()
    await expect(page.getByText(/tool calls?/).first()).toBeVisible()
  })

  test('deny keeps the gated tool from running', async ({ page }) => {
    const id = stamp()
    await ensureTestCredential(page, `Deny model ${id}`)
    await createProject(page, `Deny ${id}`)
    await page.getByRole('button', { name: /choose a model/i }).click()
    await page.getByRole('button', { name: `Deny model ${id}` }).click()
    await page
      .getByRole('button', { name: /Test model \(canned answers\)/ })
      .first()
      .click()
    await expect(page.getByRole('button', { name: /^test$/i })).toBeVisible()

    const composer = page.getByPlaceholder(/send a message/i)
    await composer.fill('Please run the demo action')
    await composer.press('Enter')
    await page.getByRole('button', { name: 'Deny', exact: true }).click({ timeout: 30_000 })
    await expect(page.getByText(/denied/i).first()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/Approved and performed/)).toHaveCount(0)
  })
})

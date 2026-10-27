import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PEOPLE = path.join(HERE, 'fixtures/people.csv')
const stamp = () => new Date().toISOString().replace(/\D/g, '').slice(0, 14)

async function createWorkspace(page: Page, name: string) {
  await page.goto('/')
  await page.getByRole('heading', { name: 'ECHOLOT Workbench' }).waitFor()
  await page.getByRole('button', { name: /new workspace/i }).click()
  await page.getByLabel('Name').fill(name)
  await page.getByRole('button', { name: 'Create' }).click()
  await page.getByRole('link', { name: new RegExp(name) }).click()
  await page.waitForURL(/\/w\//)
  await page.getByText('Nothing selected').waitFor()
}

async function uploadPeople(page: Page) {
  await page.locator('input[type=file]').setInputFiles(PEOPLE)
  await expect(page.getByText('120 records')).toBeVisible()
}

test('upload a CSV and browse its records', async ({ page }) => {
  await createWorkspace(page, `Records ${stamp()}`)
  await uploadPeople(page)
  await page.getByRole('button', { name: /^people\.csv/ }).click()
  await expect(page.getByText('rows 0–49 of 120')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Eduardo Chillida', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Next page' }).click()
  await expect(page.getByText('rows 50–99 of 120')).toBeVisible()
  await page.getByLabel('Go to row').fill('110')
  await page.getByLabel('Go to row').press('Enter')
  await expect(page.getByRole('cell', { name: 'Person 110', exact: true })).toBeVisible()
  await page.getByRole('cell', { name: 'Person 110', exact: true }).click()
  await expect(page.locator('tr[aria-selected=true]')).toHaveAttribute('data-row', '110')
})

test('chat with the test model and answer an approval', async ({ page }) => {
  await createWorkspace(page, `Chat ${stamp()}`)
  await uploadPeople(page)
  const composer = page.getByPlaceholder('Send a message...')
  await composer.fill('What files do I have?')
  await composer.press('Enter')
  // the test model calls every tool it sees; the demo tool waits for approval
  const allow = page.getByRole('button', { name: 'Allow', exact: true })
  await expect(allow.first()).toBeVisible({ timeout: 30_000 })
  await allow.first().click()
  await expect(page.getByText(/Approved and performed: demo/).first()).toBeVisible({
    timeout: 30_000,
  })
  // the test model also wrote the task notes ("a") and opened them in the middle pane
  await expect(page.getByLabel('Task notes').locator('.cm-content')).toHaveText('a')
  // the conversation survives a reload, with the tool results
  await page.reload()
  // .last(): the conversation title in the picker repeats the first message
  await expect(page.getByText('What files do I have?', { exact: true }).last()).toBeVisible()
  await expect(page.getByText(/Approved and performed: demo/).first()).toBeVisible()
})

test('write and save the task notes', async ({ page }) => {
  await createWorkspace(page, `Task ${stamp()}`)
  await page.getByRole('button', { name: 'Task notes' }).click()
  await expect(page.getByText('nothing saved yet')).toBeVisible()
  // CodeMirror puts the label on its wrapper; the editable element is .cm-content
  await page.getByLabel('Task notes').locator('.cm-content').click()
  await page.keyboard.press('ControlOrMeta+A')
  await page.keyboard.type('# Task\nLink every person to Wikidata.')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Task notes saved')).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: 'Task notes' }).click()
  await expect(page.getByText('Link every person to Wikidata.')).toBeVisible()
})

test('write a script, run a sample, read the results, export', async ({ page }) => {
  await createWorkspace(page, `Run ${stamp()}`)
  await uploadPeople(page)
  await page.getByRole('button', { name: /^Script/ }).click()
  await expect(page.getByText('no version yet')).toBeVisible()
  await page.getByLabel('Script code').locator('.cm-content').click()
  await page.keyboard.press('ControlOrMeta+A')
  await page.keyboard.insertText(
    [
      'def process(record, ctx):',
      '    if record["name"] == "Jorge Oteiza":',
      '        raise ValueError("no idea who this is")',
      '    return {"name_upper": record["name"].upper()}',
      '',
    ].join('\n'),
  )
  await page.getByLabel('Version note').fill('upper-case the names')
  await page.getByRole('button', { name: 'Save as new version' }).click()
  await expect(page.getByText('Saved as version 1')).toBeVisible()

  await page.getByRole('button', { name: 'Run 20' }).click()
  await expect(page.getByTestId('run-status').first()).toHaveText('done', { timeout: 60_000 })
  await expect(page.getByTestId('run-counts')).toHaveText('19 ok · 1 error · 20 records')
  await expect(page.getByRole('cell', { name: "BRIAN O'DOHERTY", exact: true })).toBeVisible()

  await page.getByLabel('Show results').selectOption('error')
  await expect(page.getByRole('cell', { name: 'ValueError: no idea who this is' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Eduardo Chillida', exact: true })).toHaveCount(0)

  const download = page.waitForEvent('download')
  await page.getByLabel('Export CSV').click()
  const csv = await (await download).createReadStream()
  const chunks: Buffer[] = []
  for await (const chunk of csv) chunks.push(chunk as Buffer)
  const lines = Buffer.concat(chunks).toString('utf-8').trim().split(/\r?\n/)
  expect(lines[0]).toBe('uri,name,entity_type,context,name_upper,status,error')
  expect(lines).toHaveLength(21)
  expect(lines[3]).toContain('Jorge Oteiza')
  expect(lines[3]).toContain('error,ValueError: no idea who this is')
})

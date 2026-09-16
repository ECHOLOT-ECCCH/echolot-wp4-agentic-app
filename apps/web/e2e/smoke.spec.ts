import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

const HERE = path.dirname(fileURLToPath(import.meta.url))

const SAMPLES = path.resolve(HERE, '../../../packages/pipeline/src/elexifier_pipeline/sample')
const stamp = () => new Date().toISOString().replace(/\D/g, '').slice(0, 14)

async function createProject(page: Page, name: string, sourceType: 'xml' | 'pdf' = 'xml') {
  await page.goto('/')
  await page.getByRole('heading', { name: 'Elexifier' }).waitFor()
  await page.getByRole('button', { name: /new project/i }).click()
  await page.getByLabel('Name').fill(name)
  if (sourceType === 'pdf') await page.getByRole('button', { name: /^PDF/ }).click()
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

  test('mapping editor: save a mapping, run a sample, see the output next to the source', async ({
    page,
  }) => {
    const id = stamp()
    await createProject(page, `Mapping ${id}`)
    await page
      .locator('input[type=file]')
      .setInputFiles(path.join(SAMPLES, 'sample-dictionary.xml'))
    await expect(
      page.getByRole('button', { name: /^sample-dictionary\.xml.*entries$/ }),
    ).toBeVisible({ timeout: 90_000 })

    // the editor opens with a template that happens to describe the sample dictionary
    await page.getByRole('button', { name: /^Mapping/ }).click()
    await expect(page.getByText('no version yet, this is a template')).toBeVisible()
    const meta = '{http://elex.is/wp1/teiLex0Mapper/meta}'
    const mapping = {
      inSelector: '/dictionary',
      outElement: 'lexicographicResource',
      textVals: [
        { attribute: `${meta}constant`, constant: 'urn:sample', outElement: 'uri' },
        { attribute: 'lang', outElement: 'langCode' },
      ],
      children: [
        {
          inSelector: 'entry',
          outElement: 'entry',
          textVals: [
            { inSelector: 'form/orth', attribute: `${meta}innerText`, outElement: 'headword' },
            { attribute: `${meta}autogenerated`, outElement: 'id' },
          ],
          children: [
            {
              inSelector: 'gramGrp/pos',
              outElement: 'partOfSpeech',
              textVals: [{ attribute: `${meta}innerText`, outElement: 'tag' }],
            },
            {
              inSelector: 'sense',
              outElement: 'sense',
              textVals: [{ attribute: `${meta}autogenerated`, outElement: 'id' }],
              children: [
                {
                  inSelector: 'def',
                  outElement: 'definition',
                  textVals: [{ attribute: `${meta}innerText`, outElement: 'text' }],
                },
              ],
            },
          ],
        },
      ],
    }
    const editor = page.locator('.cm-content').first()
    await editor.click()
    await page.keyboard.press('ControlOrMeta+A')
    await page.keyboard.insertText(JSON.stringify(mapping, null, 2))
    await page.getByLabel('Version note').fill('typed in the editor')
    const save = page.getByRole('button', { name: /^Save as v1/ })
    await expect(save).toBeEnabled({ timeout: 15_000 })
    await save.click()
    await expect(page.getByText('Mapping saved as version 1')).toBeVisible()
    await expect(page.getByLabel('Mapping version')).toHaveValue('1')

    await page.getByRole('button', { name: /Run sample/ }).click()
    await expect(page.getByText(/^Sample run$/i)).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('ul.min-h-0 li button')).toHaveCount(2)
    await page.getByRole('button', { name: /hiša/ }).first().click()
    await expect(page.getByText('DMLex output')).toBeVisible()
    await expect(
      page.locator('article').getByText('stavba, v kateri ljudje prebivajo'),
    ).toBeVisible()
    await expect(page.locator('article').getByText('sam.')).toBeVisible()

    // the spec editor saves too
    await page.getByRole('button', { name: /^Specification/ }).click()
    await expect(page.getByText('nothing saved yet')).toBeVisible()
    await page.locator('.cm-content').first().click()
    await page.keyboard.press('ControlOrMeta+A')
    await page.keyboard.insertText('# Sample spec\n\n- entries are <entry>')
    await page.getByRole('button', { name: /^Save$/ }).click()
    await expect(page.getByText('Specification saved as version 1')).toBeVisible()
  })

  test('pdf project: upload, layout job, page facts and viewer', async ({ page }) => {
    const id = stamp()
    await createProject(page, `PDF ${id}`, 'pdf')
    await page
      .locator('input[type=file]')
      .setInputFiles(path.join(SAMPLES, 'columns-dictionary.pdf'))
    const document = page.getByRole('button', { name: /^columns-dictionary\.pdf.*pages$/ })
    await expect(document, 'layout job did not finish; is `make worker` running?').toBeVisible({
      timeout: 90_000,
    })
    await expect(document).toContainText('4 pages')
    await document.click()
    await expect(page.getByText(/1 page without a text layer/)).toBeVisible()
    await expect(page.locator('.react-pdf__Page').first()).toBeVisible()
    await expect(page.getByText('1 / 4')).toBeVisible()
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

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

    // segment from the review panel: boxes appear on page 1, the list fills
    await page.getByRole('button', { name: /^Segment$/ }).click()
    await expect(page.getByText(/130 entry spans proposed/)).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('130 spans')).toBeVisible()
    const overlay = page.locator('[data-testid="overlay-layer"]').first()
    await expect(overlay.locator('g[data-status="proposed"]')).toHaveCount(52)

    // keyboard review: select the first span, accept it, reject the next one
    await page.getByRole('button', { name: /^p1 abacus proposed$/ }).click()
    await page.keyboard.press('a')
    await expect(page.getByText('129 spans')).toBeVisible()
    await page.keyboard.press('r')
    await expect(page.getByText('128 spans')).toBeVisible()
    await expect(overlay.locator('g[data-status="accepted"]')).toHaveCount(1)
    await page.getByRole('button', { name: 'Accepted' }).click()
    await expect(page.getByRole('button', { name: /^p1 abacus accepted$/ })).toBeVisible()
    await page.getByRole('button', { name: 'Rejected' }).click()
    await expect(page.getByRole('button', { name: /^p1 abandon rejected$/ })).toBeVisible()

    // decisions survive a reload
    await page.reload()
    await page.getByRole('button', { name: /^columns-dictionary\.pdf.*pages/ }).click()
    await page.getByRole('button', { name: 'Accepted' }).click()
    await expect(page.getByRole('button', { name: /^p1 abacus accepted$/ })).toBeVisible()
    await expect(page.getByText('1 spans')).toBeVisible()

    // edit mode: split a span at its last line, merge it back, draw a box, delete it
    await page.getByRole('button', { name: 'All' }).click()
    await expect(page.getByText('130 spans')).toBeVisible()
    await page.getByRole('button', { name: 'Edit boxes' }).click()
    await page.getByRole('button', { name: /^p1 abbey proposed$/ }).click()
    const abase = page.locator('[data-testid="overlay-layer"] g[data-annotation]').filter({
      has: page.locator('title', { hasText: /^abbey ·/ }),
    })
    await expect(abase).toHaveCount(1)
    // selecting a row scrolls the viewer; wait until the box stops moving
    const rect = abase.locator('rect').first()
    let box = await rect.boundingBox()
    for (let i = 0; i < 20; i++) {
      await page.waitForTimeout(150)
      const again = await rect.boundingBox()
      if (box && again && Math.abs(again.y - box.y) < 0.5) break
      box = again
    }
    if (!box) throw new Error('no box for abase')
    await page.getByRole('button', { name: 'Split' }).click()
    await page.mouse.click(box.x + 10, box.y + box.height * 0.85)
    await expect(page.getByText('131 spans')).toBeVisible()
    await page.getByRole('button', { name: 'Merge with next' }).click()
    await expect(page.getByText('130 spans')).toBeVisible()
    await expect(page.getByRole('button', { name: /^p1 abbey yours$/ })).toBeVisible()

    await page.getByRole('button', { name: 'Draw' }).click()
    const overlayBox = await overlay.boundingBox()
    if (!overlayBox) throw new Error('no overlay box')
    const drawTop = overlayBox.y + overlayBox.height * 0.955 // below the text, above the foot
    await page.mouse.move(overlayBox.x + overlayBox.width * 0.1, drawTop)
    await page.mouse.down()
    await page.mouse.move(overlayBox.x + overlayBox.width * 0.4, drawTop + 12, { steps: 4 })
    await page.mouse.up()
    await expect(page.getByText('131 spans')).toBeVisible()
    await page.getByRole('button', { name: 'Delete box' }).click()
    await expect(page.getByText('130 spans')).toBeVisible()
    await page.getByRole('button', { name: 'Done editing' }).click()

    // select a region for the chat: the words inside become a chip on the composer
    await page.getByRole('button', { name: 'Select for chat' }).click()
    const pageBox = await overlay.boundingBox()
    if (!pageBox) throw new Error('no page box')
    await page.mouse.move(pageBox.x + pageBox.width * 0.08, pageBox.y + pageBox.height * 0.055)
    await page.mouse.down()
    await page.mouse.move(pageBox.x + pageBox.width * 0.45, pageBox.y + pageBox.height * 0.09, {
      steps: 4,
    })
    await page.mouse.up()
    const chips = page.getByTestId('context-chips')
    await expect(chips).toContainText(/page 1, \d+ words/)
    await expect(chips).toContainText(/abacus/)
    await chips.getByRole('button', { name: /Remove selection/ }).click()
    await expect(chips).toHaveCount(0)

    // review at scale: the sample strip walks the text pages; one click accepts the rest
    const sample = page.getByTestId('review-sample')
    await expect(sample).toContainText('page 1 (1 of 3)')
    await sample.getByRole('button', { name: 'Next sample page' }).click()
    await expect(page.getByText('2 / 4')).toBeVisible()
    await expect(sample).toContainText('page 2 (2 of 3)')
    await page.getByRole('button', { name: /^Accept the remaining 127$/ }).click()
    await page.getByRole('button', { name: 'Yes, accept' }).click()
    await expect(page.getByText(/127 proposals accepted/)).toBeVisible()
    await page.getByRole('button', { name: 'To review' }).click()
    await expect(page.getByText('0 spans')).toBeVisible()
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

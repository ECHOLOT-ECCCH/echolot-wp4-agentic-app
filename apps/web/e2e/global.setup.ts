import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { test as setup } from '@playwright/test'

const HERE = path.dirname(fileURLToPath(import.meta.url))

/**
 * Creates a signed-in browser state without going through Keycloak: the devtools CLI writes a
 * user and a session straight into the database (the same tables the OIDC callback writes).
 */
setup('create a session for the e2e user', async ({ browser }) => {
  mkdirSync(path.join(HERE, '.auth'), { recursive: true })
  const statePath = path.join(HERE, '.auth/state.json')
  if (process.env.E2E_LOGIN_VIA_KEYCLOAK) {
    // Against a deployed stack (compose acceptance): sign in through the identity provider.
    const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost'
    const page = await browser.newPage()
    await page.goto(`${baseURL}/`)
    await page.waitForURL(/realms\/elexifier/)
    await page.locator('#username').fill(process.env.E2E_USER ?? 'dev@example.com')
    await page.locator('#password').fill(process.env.E2E_PASSWORD ?? 'dev')
    await page.locator('#kc-login').click()
    await page.waitForURL((url) => url.origin === new URL(baseURL).origin)
    await page.context().storageState({ path: statePath })
    await page.close()
    return
  }
  const repoRoot = path.resolve(HERE, '../../..')
  const output = execFileSync(
    'uv',
    [
      'run',
      'python',
      '-m',
      'elexifier_api.devtools',
      'session',
      '--email',
      'e2e@example.com',
      '--name',
      'E2E User',
    ],
    { cwd: repoRoot, encoding: 'utf8' },
  )
  const { session_id, csrf } = JSON.parse(output) as { session_id: string; csrf: string }
  const host = new URL(process.env.E2E_BASE_URL ?? 'http://localhost:5173').hostname
  const cookie = (name: string, value: string) => ({
    name,
    value,
    domain: host,
    path: '/',
    expires: -1,
    httpOnly: name === 'elx_session',
    secure: false,
    sameSite: 'Lax' as const,
  })
  mkdirSync(path.join(HERE, '.auth'), { recursive: true })
  writeFileSync(
    path.join(HERE, '.auth/state.json'),
    JSON.stringify({
      cookies: [cookie('elx_session', session_id), cookie('elx_csrf', csrf)],
      origins: [],
    }),
  )
})

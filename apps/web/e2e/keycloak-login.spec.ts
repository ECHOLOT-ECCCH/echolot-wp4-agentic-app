import { expect, test } from '@playwright/test'

/**
 * The real OIDC round trip against the Keycloak realm from deploy/compose. Runs when the identity
 * provider is up (E2E_KEYCLOAK=1, `make dev-infra`); the other specs use a database-created session.
 */
test.describe('keycloak login', () => {
  test.skip(!process.env.E2E_KEYCLOAK, 'set E2E_KEYCLOAK=1 with Keycloak running')
  test.use({ storageState: { cookies: [], origins: [] } })

  test('signs in through Keycloak and lands on the projects page', async ({ page }) => {
    await page.goto('/')
    await page.waitForURL(/realms\/elexifier/)
    await page.locator('#username').fill('dev@example.com')
    await page.locator('#password').fill('dev')
    await page.locator('#kc-login').click()
    const origin = new URL(process.env.E2E_BASE_URL ?? 'http://localhost:5173').origin
    await page.waitForURL((url) => url.origin === origin && url.pathname === '/')
    await expect(page.getByRole('heading', { name: 'Elexifier' })).toBeVisible()
    const me = await page.request.get('/api/me')
    expect(me.ok()).toBeTruthy()
    expect((await me.json()).email).toBe('dev@example.com')
  })
})

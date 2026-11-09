import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockApi, renderWithProviders } from '@/test/utils'

vi.mock('@tanstack/react-router', async () => {
  const actual =
    await vi.importActual<typeof import('@tanstack/react-router')>('@tanstack/react-router')
  return {
    ...actual,
    Link: ({ children, ...rest }: { children?: React.ReactNode; to?: string }) => (
      <a href={rest.to ?? '#'}>{children}</a>
    ),
  }
})

import { ModelPicker } from '@/features/settings/ModelPicker'
import { SettingsPage } from '@/features/settings/SettingsPage'

afterEach(() => vi.unstubAllGlobals())

const me = (isAdmin: boolean) => ({
  id: 'u1',
  email: 'ana@example.org',
  name: 'Ana',
  is_admin: isAdmin,
  is_active: true,
  created_at: '2026-11-01T10:00:00Z',
  last_login_at: '2026-11-09T10:00:00Z',
})

const vendor = (name: string, label: string, hasKey: boolean) => ({
  name,
  label,
  needs_key: true,
  needs_base_url: false,
  key_url: `https://${name}.example/keys`,
  suggested: [{ id: `${name}-big`, label: `${label} Big` }],
  has_key: hasKey,
  key_hint: hasKey ? '…1234' : '',
  base_url: null,
  key_updated_at: hasKey ? '2026-11-09T10:00:00Z' : null,
  key_last_used_at: null,
})

const serverChoice = {
  provider: 'anthropic',
  model_id: 'claude-sonnet-5-5',
  source: 'server',
  server_model: { provider: 'anthropic', model_id: 'claude-sonnet-5-5' },
  ready: true,
  problem: null,
}

describe('SettingsPage', () => {
  it('stores a key without ever showing it back', async () => {
    const { calls } = mockApi([
      { path: '/api/me/model', body: serverChoice },
      { path: '/api/me', body: me(false) },
      {
        path: '/api/vendors',
        body: [vendor('anthropic', 'Anthropic (Claude)', false), vendor('openai', 'OpenAI', true)],
      },
      {
        method: 'PUT',
        path: '/api/me/keys/anthropic',
        body: vendor('anthropic', 'Anthropic (Claude)', true),
      },
    ])
    renderWithProviders(<SettingsPage />)
    const row = (await screen.findByText('Anthropic (Claude)')).closest('li')!
    expect(within(row).getByText('No key')).toBeInTheDocument()
    expect(screen.getByText('Key …1234')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Users' })).not.toBeInTheDocument()

    fireEvent.click(within(row).getByRole('button', { name: 'Add key' }))
    const input = within(row).getByLabelText('API key')
    expect(input).toHaveAttribute('type', 'password')
    fireEvent.change(input, { target: { value: 'sk-ant-secret' } })
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.url).toBe('/api/me/keys/anthropic')
    expect(put.body).toEqual({ api_key: 'sk-ant-secret' })
  })

  it('lets an admin add a user and shows the temporary password once', async () => {
    const { calls } = mockApi([
      { path: '/api/me/model', body: serverChoice },
      { path: '/api/me', body: me(true) },
      { path: '/api/vendors', body: [] },
      { path: '/api/admin/users', body: [me(true)] },
      {
        method: 'POST',
        path: '/api/admin/users',
        body: {
          user: { ...me(false), id: 'u2', email: 'bob@example.org', name: 'Bob' },
          password: 'TempPass-1234567',
        },
      },
    ])
    renderWithProviders(<SettingsPage />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Users' }))
    expect(await screen.findByText('ana@example.org')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('E-mail'), { target: { value: 'bob@example.org' } })
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Bob' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add user' }))
    expect(await screen.findByTestId('issued-password')).toHaveTextContent('TempPass-1234567')
    const post = calls.find((c) => c.method === 'POST')!
    expect(post.body).toEqual({ email: 'bob@example.org', name: 'Bob', is_admin: false })
    // nobody disables or demotes themselves from here
    expect(screen.queryByRole('button', { name: 'Disable' })).not.toBeInTheDocument()
  })
})

describe('ModelPicker', () => {
  it('chooses a model of a vendor the user has a key for', async () => {
    const { calls } = mockApi([
      {
        method: 'PUT',
        path: '/api/me/model',
        body: { ...serverChoice, provider: 'openai', model_id: 'gpt-x', source: 'own' },
      },
      { path: '/api/me/model', body: serverChoice },
      {
        path: '/api/vendors/openai/models',
        body: {
          models: [
            { id: 'gpt-big', label: 'GPT Big', suggested: true },
            { id: 'gpt-x', label: 'gpt-x', suggested: false },
          ],
          error: null,
        },
      },
      {
        path: '/api/vendors',
        body: [vendor('anthropic', 'Anthropic (Claude)', false), vendor('openai', 'OpenAI', true)],
      },
    ])
    renderWithProviders(<ModelPicker />)
    fireEvent.click(await screen.findByRole('button', { name: 'Choose the model' }))
    expect(await screen.findByText('Server default · claude-sonnet-5-5', { exact: false }))
    // only vendors with a key are offered
    expect(screen.queryByRole('button', { name: 'Anthropic (Claude)' })).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'OpenAI' }))
    fireEvent.click(await screen.findByRole('button', { name: /gpt-x/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(calls.find((c) => c.method === 'PUT')!.body).toEqual({
      provider: 'openai',
      model_id: 'gpt-x',
    })
  })
})

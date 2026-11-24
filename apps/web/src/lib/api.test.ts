import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, chatErrorMessage, safeNext } from '@/lib/api'
import { mockApi } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

describe('api', () => {
  it('sends the request header the server requires', async () => {
    const { calls } = mockApi([{ method: 'POST', path: '/api/workspaces', body: { id: 'w1' } }])
    await api('/api/workspaces', { method: 'POST', json: { name: 'x' } })
    expect(calls[0]?.headers?.['X-Echolot-Request']).toBe('1')
    expect(calls[0]?.body).toEqual({ name: 'x' })
  })

  it('only follows a sign-in to a path on this site', () => {
    expect(safeNext('/w/abc?x=1')).toBe('/w/abc?x=1')
    expect(safeNext(null)).toBe('/')
    expect(safeNext('https://evil.example')).toBe('/')
    expect(safeNext('//evil.example')).toBe('/')
    expect(safeNext('/\\evil.example')).toBe('/')
    expect(safeNext('/login?next=/x')).toBe('/')
  })

  it('shows the detail of a refused chat turn', () => {
    const refused = new Error(JSON.stringify({ detail: 'Add your OpenAI key in Settings' }))
    expect(chatErrorMessage(refused)).toBe('Add your OpenAI key in Settings')
    expect(chatErrorMessage(new Error('Failed to fetch'))).toBe('Failed to fetch')
  })
})

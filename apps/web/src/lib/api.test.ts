import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { mockApi } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

describe('api', () => {
  it('parses JSON bodies by default', async () => {
    mockApi([{ path: '/api/thing', body: { a: 1 } }])
    await expect(api('/api/thing')).resolves.toEqual({ a: 1 })
  })

  it('returns the raw text when asked, even for a JSON content type', async () => {
    const { fetchMock } = mockApi([{ path: '/api/files/f1/content', body: { step: 1 } }])
    const text = await api<string>('/api/files/f1/content', { asText: true })
    expect(text).toBe('{"step":1}')
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect((init.headers as Record<string, string>).Accept).toBe('*/*')
  })
})

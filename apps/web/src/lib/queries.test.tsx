import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { keys, useRun } from '@/lib/queries'
import { mockApi } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

describe('runs and files', () => {
  it('refetches the file list when a followed run finishes', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(keys.run('w1', 'r1'), { id: 'r1', status: 'running' })
    queryClient.setQueryData(keys.files('w1'), [])
    mockApi([{ path: '/api/workspaces/w1/runs/r1', body: { id: 'r1', status: 'done' } }])
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useRun('w1', 'r1'), { wrapper })
    await waitFor(() => expect(result.current.data?.status).toBe('done'))
    expect(queryClient.getQueryState(keys.files('w1'))?.isInvalidated).toBe(true)
  })
})

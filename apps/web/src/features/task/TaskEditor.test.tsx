import { act, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TaskEditor } from '@/features/task/TaskEditor'
import { keys } from '@/lib/queries'
import { mockApi } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

describe('TaskEditor', () => {
  it('shows notes the agent wrote meanwhile when nothing is being edited', async () => {
    mockApi([
      {
        path: '/api/workspaces/w1/task',
        body: { markdown: '# Task\nfirst', updated_at: '2026-10-27T10:00:00Z' },
      },
    ])
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <TaskEditor workspaceId="w1" />
      </QueryClientProvider>,
    )
    expect(await screen.findByText('first')).toBeInTheDocument()
    act(() =>
      queryClient.setQueryData(keys.task('w1'), {
        markdown: '# Task\nsecond, by the agent',
        updated_at: '2026-10-27T10:05:00Z',
      }),
    )
    expect(await screen.findByText('second, by the agent')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled()
  })
})

import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RunView } from '@/features/runs/RunView'
import { mockApi, renderWithProviders } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

const run = (over: Record<string, unknown>) => ({
  id: 'r1',
  workspace_id: 'w1',
  script_version: 2,
  services: ['echolot'],
  input_file_id: 'f1',
  input_file_name: 'people.csv',
  parent_run_id: null,
  record_limit: 20,
  selected_rows: null,
  concurrency: 4,
  script_params: {},
  purpose: 'Try 20 people',
  status: 'done',
  executor: 'local',
  progress: { done: 20, total: 20, ok: 19, error: 1 },
  counts: { total: 20, ok: 19, error: 1 },
  result_file_id: 'res1',
  error: null,
  submitted_by: 'agent',
  created_at: '2026-10-27T10:00:00Z',
  started_at: '2026-10-27T10:00:01Z',
  finished_at: '2026-10-27T10:00:05Z',
  ...over,
})

describe('RunView', () => {
  it('shows a finished run with outputs before inputs', async () => {
    mockApi([
      {
        path: '/api/workspaces/w1/runs/r1/rows',
        body: {
          total: 20,
          offset: 0,
          run_status: 'done',
          input_columns: ['name'],
          output_columns: ['top_qid'],
          rows: [
            {
              row: 0,
              status: 'ok',
              error: null,
              ms: 5,
              input: { name: 'Ana' },
              output: { top_qid: 'Q1' },
            },
            {
              row: 1,
              status: 'error',
              error: 'ServiceError: 502',
              ms: 9,
              input: { name: 'Jon' },
              output: {},
            },
          ],
        },
      },
      { path: '/api/workspaces/w1/runs/r1', body: run({}) },
    ])
    renderWithProviders(<RunView workspaceId="w1" runId="r1" />)
    expect(await screen.findByText('Try 20 people')).toBeInTheDocument()
    expect(screen.getByTestId('run-counts')).toHaveTextContent('19 ok · 1 error · 20 records')
    expect(
      screen.getByText(/Script v2 on people\.csv · calls echolot · by the agent/),
    ).toBeInTheDocument()
    expect(screen.getByText(/took 4\.0 s/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry 1 failed/i })).toBeInTheDocument()
    expect(await screen.findByText('Q1')).toBeInTheDocument()
    const headers = within(screen.getByRole('table')).getAllByRole('columnheader')
    expect(headers.map((h) => h.textContent)).toEqual(['#', 'status', 'top_qid', 'error', 'name'])
    expect(screen.getByText('ServiceError: 502')).toBeInTheDocument()
  })

  it('shows progress while running and the reason when it failed', async () => {
    mockApi([
      {
        path: '/api/workspaces/w1/runs/r1',
        body: run({
          status: 'running',
          progress: { done: 5, total: 50 },
          counts: {},
          result_file_id: null,
        }),
      },
    ])
    renderWithProviders(<RunView workspaceId="w1" runId="r1" />)
    expect(await screen.findByText('5 / 50')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
    expect(screen.getByText('Results appear here when the run is done.')).toBeInTheDocument()
  })
})

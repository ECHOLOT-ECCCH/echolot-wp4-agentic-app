import { fireEvent, screen, waitFor, within } from '@testing-library/react'
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

  it('records a decision per row from the candidates', async () => {
    const candidates = [
      { url: 'http://en.wikipedia.org/wiki/A', wikidata_item_id: 'Q1', title: 'Ana', score: 0.9 },
      { url: 'http://en.wikipedia.org/wiki/B', wikidata_item_id: 'Q2', title: 'Ane', score: 0.4 },
    ]
    const { calls, fetchMock } = mockApi([
      { method: 'PUT', path: '/api/workspaces/w1/runs/r1/rows/0/decision', body: {} },
      {
        path: '/api/workspaces/w1/runs/r1/rows',
        body: {
          total: 1,
          offset: 0,
          run_status: 'done',
          input_columns: ['name'],
          output_columns: ['verdict', 'matches'],
          rows: [
            {
              row: 0,
              status: 'ok',
              error: null,
              ms: 5,
              input: { name: 'Ana' },
              output: { verdict: 'new', matches: candidates },
              decision: null,
            },
          ],
        },
      },
      { path: '/api/workspaces/w1/runs/r1', body: run({}) },
    ])
    renderWithProviders(<RunView workspaceId="w1" runId="r1" />)
    const select = await screen.findByLabelText('Decision for row 0')
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['—', '1. Q1 Ana (0.90)', '2. Q2 Ane (0.40)', 'none of these'])
    fireEvent.change(select, { target: { value: 'Q2' } })
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'PUT' && c.url.endsWith('/rows/0/decision'))).toBe(
        true,
      ),
    )
    const put = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit | undefined)?.method === 'PUT',
    )
    expect(put).toBeDefined()
    const init = (put as unknown[])[1] as RequestInit
    expect(JSON.parse(String(init.body))).toEqual({ qid: 'Q2' })
    expect(screen.getByRole('option', { name: 'verdict: conflict' })).toBeInTheDocument()
  })

  it('offers the candidates of every algorithm column in one decision', async () => {
    const m = (algorithm: string, qid: string, score: number) => ({
      url: `http://www.wikidata.org/entity/${qid}`,
      wikidata_item_id: qid,
      title: qid === 'Q937' ? 'Albert Einstein' : 'Someone',
      score,
      algorithm,
    })
    mockApi([
      {
        path: '/api/workspaces/w1/runs/r1/rows',
        body: {
          total: 1,
          offset: 0,
          run_status: 'done',
          input_columns: ['name'],
          output_columns: ['agreement', 'wikifier', 'llm_based', 'rule_based'],
          rows: [
            {
              row: 0,
              status: 'ok',
              error: null,
              ms: 5,
              input: { name: 'Albert Einstein' },
              output: {
                agreement: 'conflict',
                wikifier: [m('wikifier', 'Q937', 0.98)],
                llm_based: [m('llm_based', 'Q937', 1)],
                rule_based: [m('rule_based', 'Q5', 0.91)],
              },
              decision: null,
            },
          ],
        },
      },
      { path: '/api/workspaces/w1/runs/r1', body: run({}) },
    ])
    renderWithProviders(<RunView workspaceId="w1" runId="r1" />)
    const select = await screen.findByLabelText('Decision for row 0')
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual([
      '—',
      '1. Q937 Albert Einstein (wikifier 0.98, llm_based selected)',
      '2. Q5 Someone (rule_based 0.91)',
      'none of these',
    ])
    expect(screen.getAllByText('selected')).toHaveLength(1) // the llm_based cell, not 1.00
  })
})

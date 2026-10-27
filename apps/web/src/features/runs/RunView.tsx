import { DownloadIcon, RotateCcwIcon, SquareIcon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Pager } from '@/features/preview/RecordsTable'
import { RunStatusBadge } from '@/features/runs/RunStatusBadge'
import { ApiError } from '@/lib/api'
import { cellText, formatDate } from '@/lib/format'
import { PAGE_SIZE, pageStart } from '@/lib/paging'
import { runExportUrl, useCancelRun, useRetryFailed, useRun, useRunRows } from '@/lib/queries'
import { isTerminal, type RunRecord, type RunRow } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

function duration(run: RunRecord): string | null {
  if (!run.started_at || !run.finished_at) return null
  const seconds = (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000
  return seconds < 60 ? `${seconds.toFixed(1)} s` : `${(seconds / 60).toFixed(1)} min`
}

/** A run: what it is, how far it got, and its results next to their input records. */
export function RunView({
  workspaceId,
  runId,
  row,
}: {
  workspaceId: string
  runId: string
  row?: number
}) {
  const run = useRun(workspaceId, runId)
  const cancel = useCancelRun(workspaceId)
  const retry = useRetryFailed(workspaceId)
  const open = usePreview((s) => s.open)
  const [filter, setFilter] = useState<'ok' | 'error' | null>(null)
  const [paging, setPaging] = useState({ row, offset: row === undefined ? 0 : pageStart(row) })
  if (paging.row !== row) {
    setPaging({ row, offset: row === undefined ? paging.offset : pageStart(row) })
  }
  const rows = useRunRows(workspaceId, run.data, paging.offset, PAGE_SIZE, filter)

  if (run.isPending) return <p className="text-muted-foreground p-3 text-sm">Loading the run…</p>
  if (run.isError) return <p className="text-destructive p-3 text-sm">{run.error.message}</p>

  const data = run.data
  const total = data.progress.total ?? 0
  const done = data.progress.done ?? 0
  const errors = data.counts.error ?? data.progress.error ?? 0
  const finished = isTerminal(data.status)
  const onError = (error: Error) =>
    toast.error(error instanceof ApiError ? error.detail : error.message)

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-1 border-b px-3 py-2 text-xs">
        <div className="flex items-center gap-2">
          <RunStatusBadge status={data.status} />
          <span className="truncate font-medium">{data.purpose ?? 'Run'}</span>
          <span className="ml-auto flex items-center gap-1">
            {!finished && (
              <Button
                size="xs"
                variant="outline"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate(data.id, { onError })}
              >
                <SquareIcon /> Cancel
              </Button>
            )}
            {finished && errors > 0 && data.result_file_id && (
              <Button
                size="xs"
                variant="outline"
                disabled={retry.isPending}
                onClick={() =>
                  retry.mutate(data.id, {
                    onSuccess: (next) => open({ kind: 'run', runId: next.id }),
                    onError,
                  })
                }
              >
                <RotateCcwIcon /> Retry {errors} failed
              </Button>
            )}
            {data.result_file_id && (
              <>
                <Button
                  size="xs"
                  variant="outline"
                  nativeButton={false}
                  render={
                    <a href={runExportUrl(workspaceId, data.id, 'csv')} aria-label="Export CSV" />
                  }
                >
                  <DownloadIcon /> CSV
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  nativeButton={false}
                  render={
                    <a
                      href={runExportUrl(workspaceId, data.id, 'jsonl')}
                      aria-label="Export JSONL"
                    />
                  }
                >
                  JSONL
                </Button>
              </>
            )}
          </span>
        </div>
        <p className="text-muted-foreground">
          Script v{data.script_version ?? '?'} on {data.input_file_name ?? 'a deleted file'}
          {data.services.length > 0 && ` · calls ${data.services.join(', ')}`} · by{' '}
          {data.submitted_by === 'agent' ? 'the agent' : 'you'} · {formatDate(data.created_at)}
          {duration(data) && ` · took ${duration(data)}`}
        </p>
        {!finished && (
          <div className="flex items-center gap-2">
            <Progress value={total ? Math.round((done / total) * 100) : 0} className="flex-1" />
            <span className="text-muted-foreground tabular-nums">
              {done.toLocaleString()} / {total.toLocaleString()}
            </span>
          </div>
        )}
        {data.status === 'done' && (
          <p data-testid="run-counts">
            {(data.counts.ok ?? 0).toLocaleString()} ok · {errors.toLocaleString()} error
            {errors === 1 ? '' : 's'} · {(data.counts.total ?? 0).toLocaleString()} records
          </p>
        )}
        {data.error && <p className="text-destructive">{data.error}</p>}
      </div>
      {data.result_file_id ? (
        <>
          <div className="flex h-8 shrink-0 items-center gap-2 border-b px-3 text-xs">
            <select
              aria-label="Show results"
              className="bg-background h-6 rounded-md border px-1"
              value={filter ?? ''}
              onChange={(e) => {
                setFilter((e.target.value || null) as 'ok' | 'error' | null)
                setPaging((p) => ({ ...p, offset: 0 }))
              }}
            >
              <option value="">all results</option>
              <option value="ok">ok only</option>
              <option value="error">errors only</option>
            </select>
            <Pager
              offset={paging.offset}
              total={rows.data?.total ?? 0}
              onOffset={(offset) => setPaging((p) => ({ ...p, offset }))}
            />
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <RunRowsTable
              inputColumns={rows.data?.input_columns ?? []}
              outputColumns={rows.data?.output_columns ?? []}
              rows={rows.data?.rows ?? []}
              selectedRow={row}
              onSelect={(r) => open({ kind: 'run', runId: data.id, row: r })}
            />
          </div>
        </>
      ) : (
        <p className="text-muted-foreground p-4 text-sm">
          {finished ? 'This run has no results.' : 'Results appear here when the run is done.'}
        </p>
      )}
    </div>
  )
}

/** Results first (status, outputs, error), then the input record, muted, for comparison. */
function RunRowsTable({
  inputColumns,
  outputColumns,
  rows,
  selectedRow,
  onSelect,
}: {
  inputColumns: string[]
  outputColumns: string[]
  rows: RunRow[]
  selectedRow?: number
  onSelect: (row: number) => void
}) {
  const cell = 'max-w-72 truncate border-b px-2 py-1'
  return (
    <table className="w-max min-w-full border-separate border-spacing-0 text-xs">
      <thead className="bg-background sticky top-0 z-10">
        <tr>
          <th className="text-muted-foreground border-b px-2 py-1.5 text-right font-medium">#</th>
          <th className="border-b px-2 py-1.5 text-left font-medium">status</th>
          {outputColumns.map((c) => (
            <th key={`out-${c}`} className="border-b px-2 py-1.5 text-left font-medium">
              {c}
            </th>
          ))}
          <th className="border-b px-2 py-1.5 text-left font-medium">error</th>
          {inputColumns.map((c, i) => (
            <th
              key={`in-${c}`}
              className={`text-muted-foreground border-b px-2 py-1.5 text-left font-normal ${
                i === 0 ? 'border-l' : ''
              }`}
            >
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.row}
            data-row={r.row}
            aria-selected={r.row === selectedRow}
            onClick={() => onSelect(r.row)}
            className={r.row === selectedRow ? 'bg-accent' : 'hover:bg-accent/50'}
          >
            <td className="text-muted-foreground border-b px-2 py-1 text-right tabular-nums">
              {r.row}
            </td>
            <td className={`border-b px-2 py-1 ${r.status === 'error' ? 'text-destructive' : ''}`}>
              {r.status}
            </td>
            {outputColumns.map((c) => (
              <td key={`out-${c}`} className={cell} title={cellText(r.output[c])}>
                {cellText(r.output[c])}
              </td>
            ))}
            <td className={`${cell} text-destructive`} title={r.error ?? ''}>
              {r.error}
            </td>
            {inputColumns.map((c, i) => (
              <td
                key={`in-${c}`}
                className={`${cell} text-muted-foreground ${i === 0 ? 'border-l' : ''}`}
                title={cellText(r.input[c])}
              >
                {cellText(r.input[c])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

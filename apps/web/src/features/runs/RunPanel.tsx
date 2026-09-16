import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { IssueList } from '@/features/entries/IssueList'
import { useRun, useRunEntries } from '@/lib/queries'
import { usePreview } from '@/lib/uiActions'

const PAGE = 50

export function RunPanel({ projectId, runId }: { projectId: string; runId: string }) {
  const run = useRun(projectId, runId)
  const [offset, setOffset] = useState(0)
  const [onlyIssues, setOnlyIssues] = useState(false)
  const entries = useRunEntries(projectId, runId, { offset, limit: PAGE, issues: onlyIssues })
  const openEntry = usePreview((s) => s.openEntry)

  if (run.isPending) return <p className="text-muted-foreground p-3 text-sm">Loading run…</p>
  if (run.isError) return <p className="text-destructive p-3 text-sm">{run.error.message}</p>
  const data = run.data
  const stats = data.stats
  const report = data.validation_report
  const resourceIssues = report.resource_issues ?? []
  const total = entries.data?.total ?? 0
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="font-medium capitalize">{data.kind} run</span>
        <span className="text-muted-foreground">mapping v{data.mapping_version}</span>
        <span className="text-muted-foreground">{new Date(data.created_at).toLocaleString()}</span>
        <span
          className={`ml-auto rounded px-1.5 py-0.5 ${
            data.status === 'done'
              ? 'bg-muted'
              : data.status === 'failed'
                ? 'bg-destructive/10 text-destructive'
                : 'bg-muted animate-pulse'
          }`}
        >
          {data.status}
        </span>
      </div>
      <div className="grid shrink-0 grid-cols-2 gap-x-6 gap-y-1 border-b px-3 py-2 text-sm sm:grid-cols-4">
        <Stat label="converted" value={stats.converted ?? 0} />
        <Stat
          label="with issues"
          value={stats.with_issues ?? 0}
          tone={stats.with_issues ? 'warn' : undefined}
        />
        <Stat label="errors" value={stats.errors ?? 0} tone={stats.errors ? 'error' : undefined} />
        <Stat label="warnings" value={stats.warnings ?? 0} />
      </div>
      {(report.summary || resourceIssues.length > 0 || (report.plan_warnings ?? []).length > 0) && (
        <div className="flex shrink-0 flex-col gap-1 border-b px-3 py-2 text-xs">
          {report.summary && <p className="text-muted-foreground">{report.summary}</p>}
          {(report.plan_warnings ?? []).map((w, i) => (
            <p key={i} className="text-amber-700 dark:text-amber-400">
              {w}
            </p>
          ))}
          <IssueList issues={resourceIssues} compact />
          {data.error && <p className="text-destructive">{data.error}</p>}
        </div>
      )}
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-1.5 text-xs">
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={onlyIssues}
            onChange={(e) => {
              setOnlyIssues(e.target.checked)
              setOffset(0)
            }}
          />
          only entries with issues
        </label>
        <span className="text-muted-foreground ml-auto">
          {total.toLocaleString()} entr{total === 1 ? 'y' : 'ies'}
        </span>
        {total > PAGE && (
          <>
            <Button
              variant="ghost"
              size="xs"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              Prev
            </Button>
            <Button
              variant="ghost"
              size="xs"
              disabled={offset + PAGE >= total}
              onClick={() => setOffset(offset + PAGE)}
            >
              Next
            </Button>
          </>
        )}
      </div>
      <ul className="min-h-0 flex-1 overflow-auto">
        {entries.data?.items.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              disabled={!item.entry_id}
              onClick={() =>
                item.entry_id && openEntry(item.entry_id, data.document_id ?? undefined, runId)
              }
              className="hover:bg-accent/60 flex w-full flex-col gap-0.5 border-b px-3 py-1.5 text-left text-sm"
            >
              <span className="flex items-center gap-2">
                <span className="text-muted-foreground w-10 shrink-0 text-xs">
                  {item.ordinal !== null ? item.ordinal + 1 : '–'}
                </span>
                <span className="truncate font-medium">{item.headword ?? '(no headword)'}</span>
                {item.issue_count > 0 ? (
                  <span className="ml-auto shrink-0 rounded bg-amber-100 px-1.5 text-xs text-amber-900 dark:bg-amber-900/40 dark:text-amber-200">
                    {item.issue_count} issue{item.issue_count === 1 ? '' : 's'}
                  </span>
                ) : (
                  <span className="text-muted-foreground ml-auto shrink-0 text-xs">ok</span>
                )}
              </span>
              {item.issue_count > 0 && (
                <span className="pl-12">
                  <IssueList issues={item.issues.slice(0, 3)} compact />
                </span>
              )}
            </button>
          </li>
        ))}
        {entries.data && entries.data.items.length === 0 && (
          <li className="text-muted-foreground p-3 text-sm">No entries.</li>
        )}
      </ul>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'warn' | 'error' }) {
  const color =
    tone === 'error'
      ? 'text-destructive'
      : tone === 'warn'
        ? 'text-amber-700 dark:text-amber-400'
        : ''
  return (
    <div className="flex items-baseline gap-1.5">
      <span className={`text-base font-semibold ${color}`}>{value.toLocaleString()}</span>
      <span className="text-muted-foreground text-xs">{label}</span>
    </div>
  )
}

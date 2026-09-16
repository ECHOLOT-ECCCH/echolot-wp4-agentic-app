import { useState } from 'react'

import { DownloadIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { IssueList } from '@/features/entries/IssueList'
import { fileDownloadUrl } from '@/lib/api'
import { formatBytes } from '@/lib/format'
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
  const active = data.status === 'queued' || data.status === 'running'
  const outputs = stats.output_files ?? []
  const issueCodes = Object.entries(report.issue_codes ?? {})
  const firstErrors = report.errors ?? []
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
      {active && (
        <div className="flex shrink-0 flex-col gap-1 border-b px-3 py-2 text-xs">
          <div className="flex items-center gap-2">
            <span className="capitalize">{stats.phase ?? data.status}</span>
            <span className="text-muted-foreground">
              {stats.phase === 'validating'
                ? 'checking the whole output against the DMLex schema, this takes a few minutes for large dictionaries'
                : `${(stats.done ?? 0).toLocaleString()}${
                    stats.total ? ` / ${stats.total.toLocaleString()}` : ''
                  } entries`}
            </span>
          </div>
          <div className="bg-muted h-1.5 w-full overflow-hidden rounded">
            <div
              className={`bg-primary h-full transition-all ${stats.total ? '' : 'animate-pulse'}`}
              style={{
                width: stats.total
                  ? `${Math.min(100, Math.round(((stats.done ?? 0) / stats.total) * 100))}%`
                  : '100%',
              }}
            />
          </div>
        </div>
      )}
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
      {outputs.length > 0 && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
          <span className="text-muted-foreground">Outputs</span>
          {outputs.map((file) => (
            <a
              key={file.file_id}
              href={fileDownloadUrl(projectId, file.file_id)}
              className="bg-muted hover:bg-accent inline-flex items-center gap-1 rounded-md px-2 py-1"
            >
              <DownloadIcon className="size-3" />
              DMLex {file.format.toUpperCase()}
              <span className="text-muted-foreground">{formatBytes(file.size)}</span>
            </a>
          ))}
          {stats.report_file_id && (
            <a
              href={fileDownloadUrl(projectId, stats.report_file_id)}
              className="text-muted-foreground underline"
            >
              validation report
            </a>
          )}
          {stats.homographs_numbered ? (
            <span className="text-muted-foreground ml-auto">
              {stats.homographs_numbered} homograph number(s) assigned
            </span>
          ) : null}
        </div>
      )}
      {(report.summary ||
        resourceIssues.length > 0 ||
        (report.plan_warnings ?? []).length > 0 ||
        data.error) && (
        <div className="flex shrink-0 flex-col gap-1 border-b px-3 py-2 text-xs">
          {report.summary && (
            <p className={report.valid ? 'text-muted-foreground' : 'text-destructive'}>
              {report.summary}
            </p>
          )}
          {(report.plan_warnings ?? []).map((w, i) => (
            <p key={i} className="text-amber-700 dark:text-amber-400">
              {w}
            </p>
          ))}
          <IssueList issues={resourceIssues} compact />
          {data.error && <p className="text-destructive">{data.error}</p>}
        </div>
      )}
      {issueCodes.length > 0 && (
        <div className="flex shrink-0 flex-wrap gap-x-3 gap-y-1 border-b px-3 py-2 text-xs">
          <span className="text-muted-foreground">By kind</span>
          {issueCodes.map(([code, count]) => (
            <span key={code}>
              <code>{code}</code> {count.toLocaleString()}
            </span>
          ))}
        </div>
      )}
      {data.kind === 'full' && firstErrors.length > 0 && (
        <details className="shrink-0 border-b px-3 py-2 text-xs">
          <summary className="cursor-pointer">
            First {firstErrors.length} schema error(s)
            {report.error_count && report.error_count > firstErrors.length
              ? ` of ${report.error_count.toLocaleString()}`
              : ''}
          </summary>
          <ul className="mt-1 flex max-h-60 flex-col gap-0.5 overflow-auto">
            {firstErrors.map((e, i) => (
              <li key={i}>
                <span className="text-muted-foreground">
                  {e.ordinal !== null ? `entry ${e.ordinal + 1}` : 'resource'}
                </span>{' '}
                <code>
                  {e.path.replace(/^\/lexicographicResource\/entry\[\d+\]\/?/, '') || 'entry'}
                </code>{' '}
                {e.reason}
              </li>
            ))}
          </ul>
        </details>
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

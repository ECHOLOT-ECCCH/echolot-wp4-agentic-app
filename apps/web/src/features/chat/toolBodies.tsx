import { IssueList } from '@/features/entries/IssueList'
import type { EntryIssue } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

type Data = Record<string, unknown>
export type BodyProps = { data: Data; projectId: string }

const str = (v: unknown) => (typeof v === 'string' ? v : v == null ? '' : String(v))
const num = (v: unknown) => (typeof v === 'number' ? v : 0)
const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])

interface MappingIssue {
  severity: string
  path: string
  message: string
}

function MappingIssues({ issues }: { issues: MappingIssue[] }) {
  if (issues.length === 0) return null
  return (
    <ul className="flex flex-col gap-0.5 text-xs">
      {issues.map((issue, i) => (
        <li key={i} className="flex gap-1.5">
          <span
            className={`shrink-0 rounded px-1 ${
              issue.severity === 'error'
                ? 'bg-destructive/10 text-destructive'
                : issue.severity === 'warning'
                  ? 'bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200'
                  : 'bg-muted text-muted-foreground'
            }`}
          >
            {issue.severity}
          </span>
          <span>
            <code className="text-muted-foreground mr-1">{issue.path}</code>
            {issue.message}
          </span>
        </li>
      ))}
    </ul>
  )
}

export function ProfileBody({ data }: BodyProps) {
  const candidates = arr<{ xpath: string; count: number; reason: string }>(data.entry_candidates)
  const elements = arr<{ tag: string; count: number; has_text?: boolean }>(data.elements)
  return (
    <div className="flex flex-col gap-1 text-xs">
      {candidates[0] && (
        <p>
          Entry element <code>{candidates[0].xpath}</code> ({candidates[0].count.toLocaleString()}{' '}
          entries). {candidates[0].reason}
        </p>
      )}
      {elements.length > 0 && (
        <p className="text-muted-foreground">
          Most frequent elements:{' '}
          {elements
            .slice(0, 12)
            .map((e) => `${e.tag} (${e.count.toLocaleString()})`)
            .join(', ')}
        </p>
      )}
    </div>
  )
}

export function MappingWriteBody({ data }: BodyProps) {
  const issues = arr<MappingIssue>(data.issues)
  const stored = data.stored === true
  return (
    <div className="flex flex-col gap-1 text-xs">
      <p>
        {stored ? (
          <>
            Stored as <strong>version {str(data.version)}</strong>.
          </>
        ) : (
          <span className="text-destructive">Not stored: the mapping has errors.</span>
        )}
        {issues.length > 0 && (
          <span className="text-muted-foreground"> {issues.length} finding(s):</span>
        )}
      </p>
      <MappingIssues issues={issues} />
    </div>
  )
}

export function RunConversionBody({ data }: BodyProps) {
  const openEntry = usePreview((s) => s.openEntry)
  const openRun = usePreview((s) => s.openRun)
  const runId = str(data.run_id)
  const entries = arr<{
    ordinal: number
    headword: string | null
    entry_id: string | null
    issues: EntryIssue[]
  }>(data.entries)
  const skipped = arr<{ ordinal: number; headword: string | null }>(data.skipped)
  const resourceIssues = arr<EntryIssue>(data.resource_issues)
  const planWarnings = arr<string>(data.plan_warnings)
  if (!runId) return null
  return (
    <div className="flex flex-col gap-1.5 text-xs">
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">{str(data.validation)}</span>
        <button type="button" className="ml-auto underline" onClick={() => openRun(runId)}>
          Open run
        </button>
      </div>
      {planWarnings.map((w, i) => (
        <p key={i} className="text-amber-700 dark:text-amber-400">
          {w}
        </p>
      ))}
      <IssueList issues={resourceIssues} compact />
      <ul className="flex flex-col divide-y">
        {entries.map((entry, i) => (
          <li key={i} className="flex flex-col gap-0.5 py-1">
            <span className="flex items-center gap-2">
              <span className="text-muted-foreground w-8">{num(entry.ordinal) + 1}</span>
              {entry.entry_id ? (
                <button
                  type="button"
                  className="truncate font-medium underline-offset-2 hover:underline"
                  onClick={() => openEntry(entry.entry_id!, undefined, runId)}
                >
                  {entry.headword ?? '(no headword)'}
                </button>
              ) : (
                <span className="font-medium">{entry.headword ?? '(no headword)'}</span>
              )}
              <span className="text-muted-foreground ml-auto">
                {entry.issues.length === 0 ? 'ok' : `${entry.issues.length} issue(s)`}
              </span>
            </span>
            {entry.issues.length > 0 && (
              <span className="pl-10">
                <IssueList issues={entry.issues.slice(0, 4)} compact />
              </span>
            )}
          </li>
        ))}
      </ul>
      {skipped.length > 0 && (
        <p className="text-muted-foreground">
          No output for: {skipped.map((s) => s.headword ?? `entry ${s.ordinal + 1}`).join(', ')}
        </p>
      )}
    </div>
  )
}

export function DiffRunsBody({ data }: BodyProps) {
  const openEntry = usePreview((s) => s.openEntry)
  const runB = data.run_b as { id?: string } | undefined
  const changed = arr<{
    headword: string | null
    entry_id: string | null
    issues_before: number
    issues_after: number
    changes: string[]
  }>(data.changed)
  return (
    <div className="flex flex-col gap-1 text-xs">
      {changed.map((c, i) => (
        <div key={i} className="flex flex-col gap-0.5 border-b py-1 last:border-b-0">
          <span className="flex gap-2">
            {c.entry_id && runB?.id ? (
              <button
                type="button"
                className="font-medium underline-offset-2 hover:underline"
                onClick={() => openEntry(c.entry_id!, undefined, runB.id)}
              >
                {c.headword ?? '(no headword)'}
              </button>
            ) : (
              <span className="font-medium">{c.headword ?? '(no headword)'}</span>
            )}
            <span className="text-muted-foreground ml-auto">
              issues {c.issues_before} → {c.issues_after}
            </span>
          </span>
          <ul className="text-muted-foreground pl-3 font-mono text-[11px]">
            {c.changes.map((line, j) => (
              <li key={j}>{line}</li>
            ))}
          </ul>
        </div>
      ))}
      {changed.length === 0 && <p className="text-muted-foreground">No differences.</p>}
    </div>
  )
}

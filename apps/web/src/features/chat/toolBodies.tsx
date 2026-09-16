import { useState } from 'react'

import { IssueList } from '@/features/entries/IssueList'
import { fileDownloadUrl } from '@/lib/api'
import { formatBytes } from '@/lib/format'
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
  const openMapping = usePreview((s) => s.openMapping)
  const issues = arr<MappingIssue>(data.issues)
  const stored = data.stored === true
  const version = typeof data.version === 'number' ? data.version : undefined
  return (
    <div className="flex flex-col gap-1 text-xs">
      <p>
        {stored ? (
          <>
            Stored as <strong>version {str(data.version)}</strong>.{' '}
            <button type="button" className="underline" onClick={() => openMapping(version)}>
              Open mapping
            </button>
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

/** What the lexicographer approves: the purpose in words and the snippet itself, instead of
 *  the raw JSON arguments. */
export function RunCodeArgs({ argsText }: { argsText?: string }) {
  let args: Record<string, unknown> = {}
  try {
    args = argsText ? (JSON.parse(argsText) as Record<string, unknown>) : {}
  } catch {
    return (
      <pre className="bg-muted/50 rounded-md p-2.5 text-xs whitespace-pre-wrap">{argsText}</pre>
    )
  }
  const inputs = arr<string>(args.inputs)
  const code = str(args.code)
  return (
    <div className="flex flex-col gap-1.5 text-xs" data-testid="run-code-args">
      {args.purpose ? <p className="font-medium">{str(args.purpose)}</p> : null}
      <p className="text-muted-foreground">
        {inputs.length > 0 ? `Inputs: ${inputs.join(', ')}` : 'No inputs'}
        {typeof args.timeout_s === 'number' ? ` · time limit ${args.timeout_s} s` : ''}
        {' · no network, read-only file system except out/'}
      </p>
      <pre className="bg-muted/50 max-h-80 overflow-auto rounded-md p-2.5 font-mono text-[11px] leading-snug whitespace-pre">
        {code || '(no code)'}
      </pre>
    </div>
  )
}

function LogBlock({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(label === 'stdout')
  if (!text.trim()) return null
  const lines = text.split('\n').length
  return (
    <div>
      <button
        type="button"
        className="text-muted-foreground text-xs underline"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? 'Hide' : 'Show'} {label} ({lines} line{lines === 1 ? '' : 's'})
      </button>
      {open && (
        <pre className="bg-muted mt-1 max-h-60 overflow-auto rounded-md p-2 font-mono text-[11px] leading-snug whitespace-pre-wrap">
          {text}
        </pre>
      )}
    </div>
  )
}

/** The outcome of a sandbox run: status, logs, and the outputs to open or download. */
export function RunCodeBody({ data, projectId }: BodyProps) {
  const openFile = usePreview((s) => s.openFile)
  const outputs = arr<{ file_id: string; name: string; size: number; mime?: string }>(data.outputs)
  const inputs = arr<{ name: string; mount_path: string }>(data.inputs)
  const exitCode = typeof data.exit_code === 'number' ? data.exit_code : null
  const killed = data.killed === true
  const failed = killed || (exitCode !== null && exitCode !== 0)
  return (
    <div className="flex flex-col gap-1.5 text-xs" data-testid="run-code-body">
      <p className={failed ? 'text-destructive' : 'text-muted-foreground'}>
        {killed
          ? `Killed at the time limit after ${num(data.duration_s).toFixed(1)} s`
          : `Exit code ${exitCode ?? '?'} in ${num(data.duration_s).toFixed(1)} s`}
        {inputs.length > 0 && ` · inputs: ${inputs.map((i) => i.mount_path).join(', ')}`}
        {data.logs_truncated === true && ' · logs truncated'}
      </p>
      {outputs.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {outputs.map((output) => (
            <li key={output.file_id} className="flex items-center gap-2">
              <button
                type="button"
                className="font-medium underline-offset-2 hover:underline"
                onClick={() => openFile(output.file_id)}
              >
                {output.name}
              </button>
              <span className="text-muted-foreground">{formatBytes(num(output.size))}</span>
              <a
                className="text-muted-foreground ml-auto underline"
                href={fileDownloadUrl(projectId, output.file_id)}
              >
                download
              </a>
            </li>
          ))}
        </ul>
      )}
      <LogBlock label="stdout" text={str(data.stdout)} />
      <LogBlock label="stderr" text={str(data.stderr)} />
    </div>
  )
}

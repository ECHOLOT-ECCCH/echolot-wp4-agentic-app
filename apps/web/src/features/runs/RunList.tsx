import { formatDate } from '@/lib/format'
import type { RunRecord } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'
import { RunStatusBadge } from '@/features/runs/RunStatusBadge'

function scope(run: RunRecord): string {
  const total = run.progress.total
  if (run.selected_rows !== null) return `${run.selected_rows} retried`
  if (run.record_limit !== null) return `sample of ${total ?? run.record_limit}`
  return `${total?.toLocaleString() ?? 'all'} records`
}

export function RunList({ runs }: { runs: RunRecord[] }) {
  const target = usePreview((s) => s.target)
  const open = usePreview((s) => s.open)
  if (runs.length === 0) return <p className="text-muted-foreground px-1 text-xs">No runs yet.</p>
  return (
    <ul className="flex flex-col">
      {runs.map((run) => {
        const selected = target.kind === 'run' && target.runId === run.id
        return (
          <li key={run.id}>
            <button
              type="button"
              onClick={() => open({ kind: 'run', runId: run.id })}
              title={run.purpose ?? undefined}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs ${
                selected ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
              }`}
            >
              <RunStatusBadge status={run.status} />
              <span className="truncate">
                v{run.script_version ?? '?'} · {scope(run)}
              </span>
              <span className="text-muted-foreground ml-auto shrink-0">
                {formatDate(run.created_at)}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

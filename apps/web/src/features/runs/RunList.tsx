import { PlayIcon } from 'lucide-react'

import { useRuns } from '@/lib/queries'
import { usePreview } from '@/lib/uiActions'

export function RunList({ projectId }: { projectId: string }) {
  const runs = useRuns(projectId)
  const target = usePreview((s) => s.target)
  const openRun = usePreview((s) => s.openRun)
  const activeId =
    target.kind === 'run' ? target.runId : target.kind === 'entry' ? (target.runId ?? null) : null
  if (!runs.data || runs.data.length === 0) return null
  return (
    <section className="flex flex-col gap-1">
      <h2 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
        Runs
      </h2>
      <ul className="flex flex-col">
        {runs.data.slice(0, 12).map((run) => (
          <li key={run.id}>
            <button
              type="button"
              onClick={() => openRun(run.id)}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
                run.id === activeId ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
              }`}
            >
              <PlayIcon className="text-muted-foreground size-4 shrink-0" />
              <span className="truncate">
                {run.kind === 'sample' ? 'Sample' : 'Full run'} ·{' '}
                {run.mapping_version > 0
                  ? `mapping v${run.mapping_version}`
                  : `${run.scope.structurer ?? 'structuring'} structurer`}
              </span>
              <span
                className={`ml-auto shrink-0 text-xs ${
                  run.status === 'failed' ? 'text-destructive' : 'text-muted-foreground'
                }`}
              >
                {run.status === 'done'
                  ? `${(run.stats.converted ?? 0).toLocaleString()} entries${
                      run.stats.with_issues ? `, ${run.stats.with_issues} ⚠` : ''
                    }`
                  : run.status === 'running' && run.stats.total
                    ? `${Math.round(((run.stats.done ?? 0) / run.stats.total) * 100)}%`
                    : run.status}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

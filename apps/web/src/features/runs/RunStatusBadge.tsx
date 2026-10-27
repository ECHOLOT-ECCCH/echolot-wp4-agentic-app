import { Badge } from '@/components/ui/badge'
import type { RunStatus } from '@/lib/types'

const STYLES: Record<RunStatus, string> = {
  queued: 'text-muted-foreground',
  running: 'border-sky-400 text-sky-700 dark:text-sky-300',
  done: 'border-emerald-400 text-emerald-700 dark:text-emerald-300',
  failed: 'border-red-400 text-red-700 dark:text-red-300',
  cancelled: 'text-muted-foreground',
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <Badge variant="outline" className={STYLES[status]} data-testid="run-status">
      {status}
    </Badge>
  )
}

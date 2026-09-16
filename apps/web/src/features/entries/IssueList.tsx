import { AlertCircleIcon, AlertTriangleIcon } from 'lucide-react'

import type { EntryIssue } from '@/lib/types'

export function IssueList({
  issues,
  compact = false,
}: {
  issues: EntryIssue[]
  compact?: boolean
}) {
  if (issues.length === 0) return null
  return (
    <ul className={`flex flex-col ${compact ? 'gap-0.5' : 'gap-1'}`}>
      {issues.map((issue, i) => {
        const error = issue.severity === 'error'
        const Icon = error ? AlertCircleIcon : AlertTriangleIcon
        return (
          <li key={i} className="flex items-start gap-1.5 text-xs">
            <Icon
              className={`mt-0.5 size-3.5 shrink-0 ${error ? 'text-destructive' : 'text-amber-600'}`}
            />
            <span>
              {issue.path && issue.path !== 'entry' && (
                <code className="text-muted-foreground mr-1">{issue.path}</code>
              )}
              {issue.message}
              {!compact && (
                <span className="text-muted-foreground ml-1">
                  ({issue.source}
                  {issue.code !== 'schema' ? `: ${issue.code}` : ''})
                </span>
              )}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

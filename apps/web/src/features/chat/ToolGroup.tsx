import { useAuiState } from '@assistant-ui/react'
import type { PropsWithChildren } from 'react'

import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from '@/components/assistant-ui/elements/tool-group.aui'
import type { ThreadGroupPart } from '@/components/assistant-ui/elements/thread.aui'

/**
 * Collapsed group of consecutive tool calls that opens itself while any call inside is waiting
 * for the lexicographer (approval), so a decision is never hidden behind a "3 tool calls" toggle.
 */
export function ApprovalAwareToolGroup({
  group,
  children,
}: PropsWithChildren<{ group: ThreadGroupPart }>) {
  const needsAction = useAuiState((state) => {
    const parts = state.message?.parts ?? []
    return group.indices.some((index) => {
      const part = parts[index] as { status?: { type?: string } } | undefined
      return part?.status?.type === 'requires-action'
    })
  })
  const running = group.status.type === 'running'
  return (
    <ToolGroupRoot variant="ghost" {...(needsAction ? { open: true } : {})}>
      <ToolGroupTrigger count={group.indices.length} active={running} />
      <ToolGroupContent>{children}</ToolGroupContent>
    </ToolGroupRoot>
  )
}

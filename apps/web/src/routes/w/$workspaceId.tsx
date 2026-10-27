import { createFileRoute } from '@tanstack/react-router'

import { ThreePane } from '@/app/ThreePane'
import { ChatPane } from '@/features/chat/ChatPane'
import { PreviewErrorBoundary } from '@/features/preview/PreviewErrorBoundary'
import { PreviewHost } from '@/features/preview/PreviewHost'
import { LeftPane } from '@/features/workspace/LeftPane'
import { useWorkspace } from '@/lib/queries'

export const Route = createFileRoute('/w/$workspaceId')({
  component: WorkspaceRoute,
})

function WorkspaceRoute() {
  const { workspaceId } = Route.useParams()
  const workspace = useWorkspace(workspaceId)

  if (workspace.isPending) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
        Loading workspace…
      </div>
    )
  }
  if (workspace.isError) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center">
        <p className="text-destructive text-sm">{workspace.error.message}</p>
      </div>
    )
  }
  return (
    <ThreePane
      header={<span className="truncate text-sm font-medium">{workspace.data.name}</span>}
      left={<LeftPane workspace={workspace.data} />}
      middle={
        <PreviewErrorBoundary>
          <PreviewHost workspaceId={workspaceId} />
        </PreviewErrorBoundary>
      }
      right={<ChatPane workspace={workspace.data} />}
    />
  )
}

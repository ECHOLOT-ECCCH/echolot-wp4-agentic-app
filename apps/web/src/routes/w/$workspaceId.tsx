import { createFileRoute } from '@tanstack/react-router'

import { ThreePane } from '@/app/ThreePane'
import { UserMenu } from '@/features/auth/UserMenu'
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
      header={
        <div className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-sm font-medium">{workspace.data.name}</span>
          <UserMenu />
        </div>
      }
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

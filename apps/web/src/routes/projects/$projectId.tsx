import { createFileRoute } from '@tanstack/react-router'

import { ThreePane } from '@/app/ThreePane'
import { AuthGuard } from '@/features/auth/AuthGuard'
import { UserMenu } from '@/features/auth/UserMenu'
import { ChatPane } from '@/features/chat/ChatPane'
import { PreviewHost } from '@/features/preview/PreviewHost'
import { LeftPane } from '@/features/project/LeftPane'
import { useProject } from '@/lib/queries'
import { useProjectEvents } from '@/lib/sse'

export const Route = createFileRoute('/projects/$projectId')({
  component: () => (
    <AuthGuard>
      <Workspace />
    </AuthGuard>
  ),
})

function Workspace() {
  const { projectId } = Route.useParams()
  const project = useProject(projectId)
  useProjectEvents(projectId)

  if (project.isPending) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
        Loading project…
      </div>
    )
  }
  if (project.isError) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center">
        <p className="text-destructive text-sm">{project.error.message}</p>
      </div>
    )
  }
  return (
    <ThreePane
      header={
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium">{project.data.name}</span>
          <UserMenu />
        </div>
      }
      left={<LeftPane project={project.data} />}
      middle={<PreviewHost projectId={projectId} />}
      right={<ChatPane project={project.data} />}
    />
  )
}

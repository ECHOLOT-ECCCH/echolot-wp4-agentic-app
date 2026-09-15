import { createFileRoute } from '@tanstack/react-router'

import { AuthGuard } from '@/features/auth/AuthGuard'
import { ProjectsPage } from '@/features/project/ProjectsPage'

export const Route = createFileRoute('/')({
  component: () => (
    <AuthGuard>
      <ProjectsPage />
    </AuthGuard>
  ),
})

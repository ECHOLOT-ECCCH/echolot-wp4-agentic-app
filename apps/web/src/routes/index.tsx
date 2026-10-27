import { createFileRoute } from '@tanstack/react-router'

import { WorkspacesPage } from '@/features/workspace/WorkspacesPage'

export const Route = createFileRoute('/')({
  component: WorkspacesPage,
})

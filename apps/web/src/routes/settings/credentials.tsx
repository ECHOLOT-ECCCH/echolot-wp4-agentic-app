import { createFileRoute } from '@tanstack/react-router'

import { AuthGuard } from '@/features/auth/AuthGuard'
import { CredentialsPage } from '@/features/settings/CredentialsPage'

export const Route = createFileRoute('/settings/credentials')({
  component: () => (
    <AuthGuard>
      <CredentialsPage />
    </AuthGuard>
  ),
})

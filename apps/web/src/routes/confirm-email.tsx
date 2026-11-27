import { createFileRoute } from '@tanstack/react-router'

import { ConfirmEmailPage } from '@/features/auth/ConfirmEmailPage'

export const Route = createFileRoute('/confirm-email')({
  component: ConfirmEmailPage,
})

import type { ReactNode } from 'react'

import { ApiError, redirectToLogin } from '@/lib/api'
import { useMe } from '@/lib/queries'

export function AuthGuard({ children }: { children: ReactNode }) {
  const me = useMe()
  if (me.isPending) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    )
  }
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      redirectToLogin()
      return (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          Redirecting to sign in…
        </div>
      )
    }
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <p className="font-medium">The server is not reachable.</p>
        <p className="text-sm text-muted-foreground">{me.error.message}</p>
      </div>
    )
  }
  return <>{children}</>
}

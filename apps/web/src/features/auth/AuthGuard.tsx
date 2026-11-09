import { useEffect, type ReactNode } from 'react'

import { ApiError, redirectToLogin } from '@/lib/api'
import { useMe } from '@/lib/queries'

/** Renders its children for a signed-in user; otherwise sends the browser to sign in. */
export function AuthGuard({ children }: { children: ReactNode }) {
  const me = useMe()
  const signedOut = me.error instanceof ApiError && me.error.status === 401

  useEffect(() => {
    if (signedOut) redirectToLogin()
  }, [signedOut])

  if (me.isPending || signedOut) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
        {signedOut ? 'Redirecting to sign in…' : 'Loading…'}
      </div>
    )
  }
  if (me.isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <p className="font-medium">The server is not reachable.</p>
        <p className="text-muted-foreground text-sm">{me.error.message}</p>
      </div>
    )
  }
  return <>{children}</>
}

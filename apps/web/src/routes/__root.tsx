import type { QueryClient } from '@tanstack/react-query'
import { createRootRouteWithContext, Outlet, useRouterState } from '@tanstack/react-router'

import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AuthGuard } from '@/features/auth/AuthGuard'

export interface RouterContext {
  queryClient: QueryClient
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: RootLayout,
})

function RootLayout() {
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  return (
    <TooltipProvider>
      <div className="bg-background text-foreground h-full">
        {/* everything but the sign-in page needs a signed-in user (docs/PLAN.md 7.11) */}
        {pathname === '/login' ? (
          <Outlet />
        ) : (
          <AuthGuard>
            <Outlet />
          </AuthGuard>
        )}
      </div>
      <Toaster richColors position="bottom-right" />
    </TooltipProvider>
  )
}

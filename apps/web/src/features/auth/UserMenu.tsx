import { Link } from '@tanstack/react-router'
import { LogOutIcon, SettingsIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useLogout, useMe } from '@/lib/queries'

export function UserMenu() {
  const me = useMe()
  const logout = useLogout()
  if (!me.data) return null

  return (
    <div className="flex shrink-0 items-center gap-1">
      <span className="text-muted-foreground hidden max-w-48 truncate text-sm sm:inline">
        {me.data.name || me.data.email}
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        nativeButton={false}
        render={<Link to="/settings" />}
        aria-label="Settings"
        title={
          me.data.is_admin
            ? 'Settings: model keys, account, users'
            : 'Settings: model keys, account'
        }
      >
        <SettingsIcon />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Sign out"
        title="Sign out"
        disabled={logout.isPending}
        onClick={() =>
          logout.mutate(undefined, { onSettled: () => window.location.assign('/login') })
        }
      >
        <LogOutIcon />
      </Button>
    </div>
  )
}

import { Link } from '@tanstack/react-router'
import { LogOutIcon, SettingsIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { csrfToken, loginUrl } from '@/lib/api'
import { useMe } from '@/lib/queries'

export function UserMenu() {
  const me = useMe()
  if (!me.data) return null

  async function logout() {
    const response = await fetch('/auth/logout', {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken() },
      redirect: 'manual',
    })
    // A 303 to the identity provider ends the SSO session; otherwise go to login.
    window.location.assign(
      response.type === 'opaqueredirect' ? response.url || loginUrl('/') : loginUrl('/'),
    )
  }

  return (
    <div className="flex items-center gap-1">
      <span className="text-muted-foreground hidden text-sm sm:inline" title={me.data.email}>
        {me.data.name || me.data.email}
      </span>
      <Button
        variant="ghost"
        size="icon"
        nativeButton={false}
        render={<Link to="/settings/credentials" />}
        aria-label="Settings"
      >
        <SettingsIcon />
      </Button>
      <Button variant="ghost" size="icon" onClick={() => void logout()} aria-label="Sign out">
        <LogOutIcon />
      </Button>
    </div>
  )
}

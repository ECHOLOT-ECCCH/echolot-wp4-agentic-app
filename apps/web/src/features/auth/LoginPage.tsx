import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { safeNext } from '@/lib/api'
import { useLogin } from '@/lib/queries'

export function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const login = useLogin()

  function submit(event: React.FormEvent) {
    event.preventDefault()
    login.mutate(
      { email: email.trim(), password },
      {
        onSuccess: () => {
          const next = new URLSearchParams(window.location.search).get('next')
          // a full load: nothing from before the sign-in survives
          window.location.assign(safeNext(next))
        },
      },
    )
  }

  return (
    <div className="flex h-full items-center justify-center p-6">
      <form
        onSubmit={submit}
        className="bg-card flex w-full max-w-sm flex-col gap-4 rounded-xl p-6 ring-1 ring-foreground/10"
      >
        <div>
          <h1 className="text-xl font-semibold tracking-tight">ECHOLOT Workbench</h1>
          <p className="text-muted-foreground text-sm">Sign in to continue</p>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="login-email">E-mail</Label>
          <Input
            id="login-email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="login-password">Password</Label>
          <Input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        {login.isError && (
          <p role="alert" className="text-destructive text-sm">
            {login.error.message}
          </p>
        )}
        <Button type="submit" disabled={login.isPending || !email.trim() || !password}>
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </Button>
        <p className="text-muted-foreground text-xs">
          No account yet? Ask the person who runs this workbench to create one for you.
        </p>
      </form>
    </div>
  )
}

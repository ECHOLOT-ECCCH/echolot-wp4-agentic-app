import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useRegister, useRegistrationInfo } from '@/lib/queries'

const MIN_PASSWORD = 12

const EMPTY = { name: '', organisation: '', email: '', purpose: '', password: '', repeat: '' }

/** Asking for an account (docs/PLAN.md 7.12): the person confirms their address by e-mail, then
 *  an admin approves the request. */
export function RegisterPage() {
  const info = useRegistrationInfo()
  const register = useRegister()
  const [form, setForm] = useState(EMPTY)
  // the hidden field: people never see it, robots fill it in
  const [website, setWebsite] = useState('')
  const [sentTo, setSentTo] = useState<string | null>(null)

  const set = (field: keyof typeof EMPTY) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [field]: event.target.value }))
  const mismatch = form.repeat !== '' && form.password !== form.repeat
  const complete =
    form.name.trim() &&
    form.organisation.trim() &&
    form.email.trim() &&
    form.purpose.trim() &&
    form.password.length >= MIN_PASSWORD &&
    form.password === form.repeat

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const email = form.email.trim()
    register.mutate(
      {
        email,
        name: form.name.trim(),
        organisation: form.organisation.trim(),
        purpose: form.purpose.trim(),
        password: form.password,
        website,
      },
      { onSuccess: () => setSentTo(email) },
    )
  }

  const reviewer = info.data?.reviewer ?? 'An administrator'

  let content: React.ReactNode
  if (info.isPending) {
    content = <p className="text-muted-foreground text-sm">Loading…</p>
  } else if (!info.data?.enabled) {
    content = (
      <p className="text-sm">
        This workbench does not take requests for accounts. Ask the person who runs it to create one
        for you.
      </p>
    )
  } else if (sentTo) {
    content = (
      <output className="flex flex-col gap-3 text-sm">
        <p className="font-medium">Check your inbox</p>
        <p>
          We have sent an e-mail to <strong>{sentTo}</strong>. Open the link in it within 24 hours
          to confirm your address and send your request for review.
        </p>
        <p className="text-muted-foreground">
          {reviewer} reviews each request. You will get another e-mail when yours is approved.
        </p>
      </output>
    )
  } else {
    content = (
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="register-name">Name</Label>
          <Input
            id="register-name"
            autoComplete="name"
            value={form.name}
            onChange={set('name')}
            maxLength={200}
            required
            autoFocus
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="register-organisation">Organisation</Label>
          <Input
            id="register-organisation"
            autoComplete="organization"
            value={form.organisation}
            onChange={set('organisation')}
            maxLength={200}
            required
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="register-email">E-mail</Label>
          <Input
            id="register-email"
            type="email"
            autoComplete="email"
            value={form.email}
            onChange={set('email')}
            required
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="register-purpose">What will you use the workbench for?</Label>
          <Textarea
            id="register-purpose"
            value={form.purpose}
            onChange={set('purpose')}
            maxLength={2000}
            rows={3}
            required
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="register-password">Password</Label>
          <Input
            id="register-password"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={set('password')}
            minLength={MIN_PASSWORD}
            required
          />
          <p className="text-muted-foreground text-xs">At least {MIN_PASSWORD} characters.</p>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="register-repeat">Password again</Label>
          <Input
            id="register-repeat"
            type="password"
            autoComplete="new-password"
            value={form.repeat}
            onChange={set('repeat')}
            aria-invalid={mismatch || undefined}
            required
          />
          {mismatch && <p className="text-destructive text-xs">The passwords differ.</p>}
        </div>
        <div aria-hidden="true" className="absolute -left-[10000px] h-px w-px overflow-hidden">
          <label>
            Website (leave this empty)
            <input
              tabIndex={-1}
              autoComplete="off"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
            />
          </label>
        </div>
        {register.isError && (
          <p role="alert" className="text-destructive text-sm">
            {register.error.message}
          </p>
        )}
        <Button type="submit" disabled={!complete || register.isPending}>
          {register.isPending ? 'Sending…' : 'Request an account'}
        </Button>
        <p className="text-muted-foreground text-xs">
          We will e-mail you a link to confirm your address. {reviewer} then reviews your request.
        </p>
      </form>
    )
  }

  return (
    <div className="flex h-full items-center justify-center overflow-y-auto p-6">
      <div className="bg-card relative flex w-full max-w-md flex-col gap-4 rounded-xl p-6 ring-1 ring-foreground/10">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">ECHOLOT Workbench</h1>
          <p className="text-muted-foreground text-sm">Request an account</p>
        </div>
        {content}
        <a href="/login" className="text-muted-foreground text-xs underline underline-offset-2">
          Back to sign in
        </a>
      </div>
    </div>
  )
}

import { Link } from '@tanstack/react-router'
import { ArrowLeftIcon, CopyIcon, ExternalLinkIcon, KeyRoundIcon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { UserMenu } from '@/features/auth/UserMenu'
import { ModelPicker } from '@/features/settings/ModelPicker'
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard'
import { formatDate } from '@/lib/format'
import {
  useChangePassword,
  useCreateUser,
  useDeleteKey,
  useMe,
  useModelChoice,
  useResetPassword,
  useSaveKey,
  useTestKey,
  useUpdateUser,
  useUsers,
  useVendors,
} from '@/lib/queries'
import type { User, Vendor } from '@/lib/types'

const MIN_PASSWORD = 12

export function SettingsPage() {
  const me = useMe()
  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col gap-6 overflow-y-auto p-6">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon-sm"
            nativeButton={false}
            render={<Link to="/" />}
            aria-label="Back to workspaces"
          >
            <ArrowLeftIcon />
          </Button>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        </div>
        <UserMenu />
      </header>
      <Tabs defaultValue="models">
        <TabsList>
          <TabsTrigger value="models">Model keys</TabsTrigger>
          <TabsTrigger value="account">Account</TabsTrigger>
          {me.data?.is_admin && <TabsTrigger value="users">Users</TabsTrigger>}
        </TabsList>
        <TabsContent value="models" className="flex flex-col gap-6 pt-2">
          <ModelSection />
        </TabsContent>
        <TabsContent value="account" className="pt-2">
          {me.data && <AccountSection user={me.data} />}
        </TabsContent>
        {me.data?.is_admin && (
          <TabsContent value="users" className="pt-2">
            <UsersSection me={me.data} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}

// --- model keys --------------------------------------------------------------------------------

function ModelSection() {
  const vendors = useVendors()
  const choice = useModelChoice()
  return (
    <>
      <section className="flex flex-col gap-2">
        <h2 className="font-medium">The agent's model</h2>
        <div className="flex flex-wrap items-center gap-2">
          <ModelPicker />
          {choice.data?.problem && (
            <span className="text-sm text-amber-700 dark:text-amber-300">
              {choice.data.problem}
            </span>
          )}
        </div>
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="font-medium">Your keys</h2>
        <p className="text-muted-foreground text-sm">
          Bring your own key for any of these vendors. Keys are stored encrypted on this server,
          used only for your conversations, and never shown again in full. Usage is billed to your
          account with the vendor.
        </p>
        {vendors.isPending && <p className="text-muted-foreground text-sm">Loading…</p>}
        {vendors.isError && <p className="text-destructive text-sm">{vendors.error.message}</p>}
        <ul className="divide-y rounded-lg border">
          {vendors.data?.map((vendor) => (
            <VendorKeyRow
              key={vendor.name}
              vendor={vendor}
              modelForTest={
                choice.data?.source === 'own' && choice.data.provider === vendor.name
                  ? choice.data.model_id
                  : undefined
              }
            />
          ))}
        </ul>
      </section>
    </>
  )
}

function VendorKeyRow({ vendor, modelForTest }: { vendor: Vendor; modelForTest?: string }) {
  const [editing, setEditing] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState(vendor.base_url ?? '')
  const save = useSaveKey()
  const remove = useDeleteKey()
  const test = useTestKey()

  function submit(event: React.FormEvent) {
    event.preventDefault()
    save.mutate(
      {
        provider: vendor.name,
        api_key: apiKey.trim() || undefined,
        base_url: vendor.needs_base_url ? baseUrl.trim() : undefined,
      },
      {
        onSuccess: () => {
          setEditing(false)
          setApiKey('')
          toast.success(`${vendor.label}: saved`)
        },
        onError: (error) => toast.error(error.message),
      },
    )
  }

  function runTest() {
    test.mutate(
      { provider: vendor.name, model_id: modelForTest },
      {
        onSuccess: (result) =>
          result.ok
            ? toast.success(`${vendor.label} works (${result.model_id}, ${result.latency_ms} ms)`)
            : toast.error(`${vendor.label}: ${result.message}`),
        onError: (error) => toast.error(error.message),
      },
    )
  }

  const canSave =
    (vendor.has_key || !vendor.needs_key || apiKey.trim().length > 0) &&
    (!vendor.needs_base_url || baseUrl.trim().length > 0)

  return (
    <li className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <KeyRoundIcon className="text-muted-foreground size-4" />
        <div className="min-w-0 flex-1">
          <div className="font-medium">{vendor.label}</div>
          <div className="text-muted-foreground text-xs">
            {vendor.has_key ? (
              <>
                {vendor.key_hint ? `Key ${vendor.key_hint}` : 'Set up'}
                {vendor.base_url && ` · ${vendor.base_url}`}
                {vendor.key_last_used_at && ` · last used ${formatDate(vendor.key_last_used_at)}`}
              </>
            ) : (
              'No key'
            )}
          </div>
        </div>
        {vendor.key_url && (
          <a
            href={vendor.key_url}
            target="_blank"
            rel="noreferrer"
            className="text-muted-foreground inline-flex items-center gap-1 text-xs underline"
          >
            Get a key <ExternalLinkIcon className="size-3" />
          </a>
        )}
        {vendor.has_key && (
          <Button size="sm" variant="outline" disabled={test.isPending} onClick={runTest}>
            {test.isPending ? 'Testing…' : 'Test'}
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={() => setEditing((v) => !v)}>
          {editing ? 'Cancel' : vendor.has_key ? 'Replace' : 'Add key'}
        </Button>
        {vendor.has_key && (
          <Button
            size="sm"
            variant="destructive"
            disabled={remove.isPending}
            onClick={() =>
              remove.mutate(vendor.name, {
                onSuccess: () => toast.success(`${vendor.label}: key removed`),
                onError: (error) => toast.error(error.message),
              })
            }
          >
            Remove
          </Button>
        )}
      </div>
      {editing && (
        <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row sm:items-end">
          {vendor.needs_base_url && (
            <div className="flex flex-1 flex-col gap-1">
              <Label htmlFor={`url-${vendor.name}`}>Base URL</Label>
              <Input
                id={`url-${vendor.name}`}
                placeholder="http://my-server:8000/v1"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            </div>
          )}
          <div className="flex flex-1 flex-col gap-1">
            <Label htmlFor={`key-${vendor.name}`}>
              API key{!vendor.needs_key && ' (optional)'}
            </Label>
            <Input
              id={`key-${vendor.name}`}
              type="password"
              autoComplete="off"
              placeholder={vendor.has_key ? 'Leave empty to keep the stored key' : ''}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoFocus
            />
          </div>
          <Button type="submit" disabled={!canSave || save.isPending}>
            Save
          </Button>
        </form>
      )}
    </li>
  )
}

// --- account -----------------------------------------------------------------------------------

function AccountSection({ user }: { user: User }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const change = useChangePassword()
  const problem =
    next && next.length < MIN_PASSWORD
      ? `At least ${MIN_PASSWORD} characters`
      : repeat && next !== repeat
        ? 'The two new passwords differ'
        : null

  function submit(event: React.FormEvent) {
    event.preventDefault()
    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          setCurrent('')
          setNext('')
          setRepeat('')
          toast.success('Password changed; other browsers are signed out')
        },
        onError: (error) => toast.error(error.message),
      },
    )
  }

  return (
    <div className="flex max-w-md flex-col gap-6">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted-foreground">E-mail</dt>
        <dd>{user.email}</dd>
        <dt className="text-muted-foreground">Name</dt>
        <dd>{user.name || '—'}</dd>
        <dt className="text-muted-foreground">Role</dt>
        <dd>{user.is_admin ? 'Admin' : 'User'}</dd>
      </dl>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <h2 className="font-medium">Change password</h2>
        <input type="text" autoComplete="username" value={user.email} readOnly hidden />
        <div className="flex flex-col gap-1">
          <Label htmlFor="pw-current">Current password</Label>
          <Input
            id="pw-current"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="pw-new">New password</Label>
          <Input
            id="pw-new"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            required
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="pw-repeat">New password again</Label>
          <Input
            id="pw-repeat"
            type="password"
            autoComplete="new-password"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            required
          />
        </div>
        {problem && <p className="text-destructive text-sm">{problem}</p>}
        <Button
          type="submit"
          className="self-start"
          disabled={!current || !next || next !== repeat || Boolean(problem) || change.isPending}
        >
          Change password
        </Button>
      </form>
    </div>
  )
}

// --- users (admins) ----------------------------------------------------------------------------

function UsersSection({ me }: { me: User }) {
  const users = useUsers(true)
  const create = useCreateUser()
  const update = useUpdateUser()
  const reset = useResetPassword()
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [admin, setAdmin] = useState(false)
  const [issued, setIssued] = useState<{ email: string; password: string } | null>(null)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    create.mutate(
      { email: email.trim(), name: name.trim(), is_admin: admin },
      {
        onSuccess: ({ user, password }) => {
          setEmail('')
          setName('')
          setAdmin(false)
          if (password) setIssued({ email: user.email, password })
        },
        onError: (error) => toast.error(error.message),
      },
    )
  }

  function change(user: User, body: { is_admin?: boolean; is_active?: boolean }) {
    update.mutate({ id: user.id, ...body }, { onError: (error) => toast.error(error.message) })
  }

  return (
    <div className="flex flex-col gap-6">
      <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="flex flex-1 flex-col gap-1">
          <Label htmlFor="new-user-email">E-mail</Label>
          <Input
            id="new-user-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>
        <div className="flex flex-1 flex-col gap-1">
          <Label htmlFor="new-user-name">Name</Label>
          <Input id="new-user-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <label className="flex h-8 items-center gap-2 text-sm">
          <input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} />
          Admin
        </label>
        <Button type="submit" disabled={!email.trim() || create.isPending}>
          Add user
        </Button>
      </form>
      {issued && <IssuedPassword {...issued} onDone={() => setIssued(null)} />}
      {users.isError && <p className="text-destructive text-sm">{users.error.message}</p>}
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="text-muted-foreground border-b text-left text-xs">
            <tr>
              <th className="p-2 font-medium">User</th>
              <th className="p-2 font-medium">Role</th>
              <th className="p-2 font-medium">Last sign-in</th>
              <th className="p-2">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {users.data?.map((user) => (
              <tr key={user.id} className={user.is_active ? '' : 'opacity-60'}>
                <td className="p-2">
                  <div className="font-medium">{user.email}</div>
                  {user.name && <div className="text-muted-foreground text-xs">{user.name}</div>}
                </td>
                <td className="p-2">
                  <div className="flex gap-1">
                    {user.is_admin ? <Badge>admin</Badge> : <Badge variant="outline">user</Badge>}
                    {!user.is_active && <Badge variant="destructive">disabled</Badge>}
                  </div>
                </td>
                <td className="text-muted-foreground p-2 text-xs">
                  {user.last_login_at ? formatDate(user.last_login_at) : 'never'}
                </td>
                <td className="p-2">
                  <div className="flex justify-end gap-1">
                    <Button
                      size="xs"
                      variant="outline"
                      disabled={reset.isPending}
                      onClick={() =>
                        reset.mutate(user.id, {
                          onSuccess: ({ password }) =>
                            password && setIssued({ email: user.email, password }),
                          onError: (error) => toast.error(error.message),
                        })
                      }
                    >
                      Reset password
                    </Button>
                    {user.id !== me.id && (
                      <>
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={update.isPending}
                          onClick={() => change(user, { is_admin: !user.is_admin })}
                        >
                          {user.is_admin ? 'Remove admin' : 'Make admin'}
                        </Button>
                        <Button
                          size="xs"
                          variant={user.is_active ? 'destructive' : 'outline'}
                          disabled={update.isPending}
                          onClick={() => change(user, { is_active: !user.is_active })}
                        >
                          {user.is_active ? 'Disable' : 'Enable'}
                        </Button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function IssuedPassword({
  email,
  password,
  onDone,
}: {
  email: string
  password: string
  onDone: () => void
}) {
  const { isCopied, copyToClipboard } = useCopyToClipboard()
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
      <p>
        Temporary password for <strong>{email}</strong>. Give it to them privately; it is shown only
        this once. They can change it under Settings → Account.
      </p>
      <div className="flex items-center gap-2">
        <code className="bg-background rounded px-2 py-1 font-mono" data-testid="issued-password">
          {password}
        </code>
        <Button size="xs" variant="outline" onClick={() => copyToClipboard(password)}>
          <CopyIcon /> {isCopied ? 'Copied' : 'Copy'}
        </Button>
        <Button size="xs" variant="ghost" onClick={onDone}>
          Done
        </Button>
      </div>
    </div>
  )
}

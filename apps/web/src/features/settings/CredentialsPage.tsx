import { Link } from '@tanstack/react-router'
import { ArrowLeftIcon, KeyRoundIcon, Trash2Icon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { UserMenu } from '@/features/auth/UserMenu'
import { formatDate } from '@/lib/format'
import {
  useCreateCredential,
  useCredentials,
  useDeleteCredential,
  useProviders,
  useTestCredential,
} from '@/lib/queries'
import type { Credential, ProbeResult, Provider } from '@/lib/types'

export function CredentialsPage() {
  const providers = useProviders()
  const credentials = useCredentials()
  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-6 overflow-y-auto p-6">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            nativeButton={false}
            render={<Link to="/" />}
            aria-label="Back"
          >
            <ArrowLeftIcon />
          </Button>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Model credentials</h1>
            <p className="text-muted-foreground text-sm">
              Bring your own keys. They are encrypted at rest and only used for your own requests.
            </p>
          </div>
        </div>
        <UserMenu />
      </header>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-medium">Your credentials</h2>
        {credentials.isPending && <p className="text-muted-foreground text-sm">Loading…</p>}
        {credentials.data?.length === 0 && (
          <p className="text-muted-foreground rounded-md border border-dashed p-4 text-sm">
            No credentials yet. Add one below; OpenRouter gives access to many models with one key.
          </p>
        )}
        {credentials.data?.map((credential) => (
          <CredentialRow
            key={credential.id}
            credential={credential}
            providers={providers.data ?? []}
          />
        ))}
      </section>

      {providers.data && <AddCredentialForm providers={providers.data} />}
    </div>
  )
}

function CredentialRow({
  credential,
  providers,
}: {
  credential: Credential
  providers: Provider[]
}) {
  const remove = useDeleteCredential()
  const test = useTestCredential()
  const [result, setResult] = useState<ProbeResult | null>(null)
  const provider = providers.find((p) => p.name === credential.provider)

  return (
    <Card size="sm">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <KeyRoundIcon className="text-muted-foreground size-4" />
              {credential.label}
              <Badge variant="secondary">{provider?.label ?? credential.provider}</Badge>
            </CardTitle>
            <CardDescription>
              {credential.key_hint ? `Key ${credential.key_hint}` : 'No key'}
              {credential.base_url ? ` · ${credential.base_url}` : ''}
              {credential.last_used_at ? ` · last used ${formatDate(credential.last_used_at)}` : ''}
            </CardDescription>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              disabled={test.isPending}
              onClick={() =>
                test.mutate(
                  { id: credential.id },
                  {
                    onSuccess: (r) => {
                      setResult(r)
                      if (r.ok) toast.success(`${r.model_id} answered in ${r.latency_ms} ms`)
                      else toast.error(r.message)
                    },
                    onError: (error) => toast.error(error.message),
                  },
                )
              }
            >
              {test.isPending ? 'Testing…' : 'Test'}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Delete credential"
              onClick={() => {
                if (confirm(`Delete credential "${credential.label}"?`)) {
                  remove.mutate(credential.id, { onError: (error) => toast.error(error.message) })
                }
              }}
            >
              <Trash2Icon />
            </Button>
          </div>
        </div>
      </CardHeader>
      {result && (
        <CardContent>
          <p className={`text-sm ${result.ok ? 'text-muted-foreground' : 'text-destructive'}`}>
            {result.ok
              ? `${result.model_id}: "${result.reply}" (${result.latency_ms} ms, ${result.input_tokens + result.output_tokens} tokens)`
              : result.message}
          </p>
        </CardContent>
      )}
    </Card>
  )
}

function AddCredentialForm({ providers }: { providers: Provider[] }) {
  const create = useCreateCredential()
  const [providerName, setProviderName] = useState(providers[0]?.name ?? 'openrouter')
  const [label, setLabel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const provider = useMemo(
    () => providers.find((p) => p.name === providerName) ?? providers[0],
    [providers, providerName],
  )

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!provider) return
    create.mutate(
      {
        provider: provider.name,
        label: label.trim() || provider.label,
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
      },
      {
        onSuccess: () => {
          setLabel('')
          setApiKey('')
          setBaseUrl('')
          toast.success('Credential saved')
        },
        onError: (error) => toast.error(error.message),
      },
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add a credential</CardTitle>
        <CardDescription>
          The key never leaves the server unencrypted and is never shown again in full.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="cred-provider">Provider</Label>
            <select
              id="cred-provider"
              value={providerName}
              onChange={(e) => setProviderName(e.target.value)}
              className="border-input bg-background h-8 rounded-lg border px-2 text-sm"
            >
              {providers.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.label}
                </option>
              ))}
            </select>
            {provider?.docs_url && (
              <a
                className="text-muted-foreground text-xs underline"
                href={provider.docs_url}
                target="_blank"
                rel="noreferrer"
              >
                Where to get a key
              </a>
            )}
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="cred-label">Label</Label>
            <Input
              id="cred-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={provider ? `My ${provider.label} key` : 'Label'}
            />
          </div>
          {provider?.needs_api_key !== false && (
            <div className="flex flex-col gap-2">
              <Label htmlFor="cred-key">
                API key {provider?.needs_api_key ? '' : '(optional)'}
              </Label>
              <Input
                id="cred-key"
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                required={provider?.needs_api_key}
              />
            </div>
          )}
          {provider?.needs_base_url && (
            <div className="flex flex-col gap-2">
              <Label htmlFor="cred-url">Base URL</Label>
              <Input
                id="cred-url"
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="http://localhost:11434/v1"
                required
              />
            </div>
          )}
          <div>
            <Button type="submit" disabled={create.isPending}>
              Save credential
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

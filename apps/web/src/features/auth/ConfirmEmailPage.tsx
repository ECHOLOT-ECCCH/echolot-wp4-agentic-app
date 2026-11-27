import { Button } from '@/components/ui/button'
import { useConfirmEmail, useRegistrationInfo } from '@/lib/queries'

/** The page behind the link in the confirmation e-mail (docs/PLAN.md 7.12). It confirms on a
 *  click, not on load: mail systems open links to check them, and must not confirm for you. */
export function ConfirmEmailPage() {
  const token = new URLSearchParams(window.location.search).get('token') ?? ''
  const info = useRegistrationInfo()
  const confirm = useConfirmEmail()
  const reviewer = info.data?.reviewer ?? 'An administrator'

  let content: React.ReactNode
  if (!token) {
    content = (
      <p className="text-sm">
        This link is incomplete. Open the link from the e-mail again, or copy all of it into the
        address bar.
      </p>
    )
  } else if (confirm.isSuccess) {
    content = (
      <output className="flex flex-col gap-3 text-sm">
        <p className="font-medium">Your address is confirmed</p>
        <p>
          {reviewer} will review your request. You will get an e-mail when it is approved; then sign
          in with your e-mail address and the password you chose.
        </p>
      </output>
    )
  } else {
    content = (
      <div className="flex flex-col gap-3">
        <p className="text-sm">Confirm your e-mail address to send your request for review.</p>
        {confirm.isError && (
          <p role="alert" className="text-destructive text-sm">
            {confirm.error.message}
          </p>
        )}
        <Button disabled={confirm.isPending} onClick={() => confirm.mutate(token)}>
          {confirm.isPending ? 'Confirming…' : 'Confirm my address'}
        </Button>
      </div>
    )
  }

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="bg-card flex w-full max-w-sm flex-col gap-4 rounded-xl p-6 ring-1 ring-foreground/10">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">ECHOLOT Workbench</h1>
          <p className="text-muted-foreground text-sm">Request an account</p>
        </div>
        {content}
        <a href="/login" className="text-muted-foreground text-xs underline underline-offset-2">
          Go to sign in
        </a>
      </div>
    </div>
  )
}

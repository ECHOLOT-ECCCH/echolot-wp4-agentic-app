import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/')({
  component: HomePage,
})

function HomePage() {
  return (
    <main className="flex h-full items-center justify-center p-6">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-semibold tracking-tight">Elexifier</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Convert legacy dictionaries to DMLex with an agent that works the way a conversion
          engineer would.
        </p>
      </div>
    </main>
  )
}

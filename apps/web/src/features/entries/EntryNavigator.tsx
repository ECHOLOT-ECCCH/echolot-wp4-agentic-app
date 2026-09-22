import { ChevronLeftIcon, ChevronRightIcon, SearchIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useEntries } from '@/lib/queries'
import type { EntryListItem } from '@/lib/types'

const PAGE = 50

export function EntryNavigator({
  projectId,
  documentId,
  selectedEntryId,
  onSelect,
}: {
  projectId: string
  documentId: string
  selectedEntryId: string | null
  onSelect: (entry: EntryListItem) => void
}) {
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [offset, setOffset] = useState(0)
  const applied = useRef('')

  // the search term settles 250 ms after the last keystroke; paging goes back to the first
  // page only when the term actually changed (the timer also fires once after mount, and must
  // not undo a page turn made in the meantime)
  useEffect(() => {
    const handle = window.setTimeout(() => {
      const next = query.trim()
      if (next === applied.current) return
      applied.current = next
      setDebounced(next)
      setOffset(0)
    }, 250)
    return () => window.clearTimeout(handle)
  }, [query])

  const entries = useEntries(projectId, documentId, { offset, limit: PAGE, q: debounced })
  const total = entries.data?.total ?? 0
  const pageStart = total === 0 ? 0 : offset + 1
  const pageEnd = Math.min(offset + PAGE, total)

  return (
    <div className="flex h-full flex-col border-r">
      <div className="relative shrink-0 p-2">
        <SearchIcon className="text-muted-foreground absolute top-4 left-4 size-3.5" />
        <Input
          className="h-8 pl-7 text-sm"
          placeholder="Search headwords"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search entries"
        />
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto" aria-label="Entries">
        {entries.data?.items.map((entry) => (
          <li key={entry.id}>
            <button
              type="button"
              onClick={() => onSelect(entry)}
              className={`flex w-full flex-col gap-0.5 border-b px-3 py-1.5 text-left ${
                entry.id === selectedEntryId ? 'bg-accent' : 'hover:bg-accent/50'
              }`}
            >
              <span className="flex items-baseline gap-2">
                <span className="text-muted-foreground w-10 shrink-0 text-[10px] tabular-nums">
                  {entry.ordinal + 1}
                </span>
                <span className="truncate text-sm font-medium">
                  {entry.headword ?? '(no headword)'}
                </span>
              </span>
              <span className="text-muted-foreground line-clamp-1 pl-12 text-xs">
                {entry.snippet}
              </span>
            </button>
          </li>
        ))}
        {entries.data && entries.data.items.length === 0 && (
          <li className="text-muted-foreground p-3 text-xs">No entries match.</li>
        )}
      </ul>
      <div className="flex shrink-0 items-center justify-between border-t px-2 py-1 text-xs">
        <span className="text-muted-foreground tabular-nums">
          {pageStart}–{pageEnd} of {total.toLocaleString()}
        </span>
        <span className="flex gap-1">
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Previous page"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            <ChevronLeftIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Next page"
            disabled={offset + PAGE >= total}
            onClick={() => setOffset(offset + PAGE)}
          >
            <ChevronRightIcon />
          </Button>
        </span>
      </div>
    </div>
  )
}

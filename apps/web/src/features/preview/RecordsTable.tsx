import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { CellValue } from '@/features/preview/CellValue'
import { cellTitle } from '@/lib/cells'
import { PAGE_SIZE, pageStart } from '@/lib/paging'
import type { RecordRow } from '@/lib/types'

/** A page of records as a table: row number first, then the columns. Clicking a row selects it
 *  (the agent then knows which row "this row" is). */
export function RecordsTable({
  columns,
  rows,
  selectedRow,
  onSelect,
}: {
  columns: string[]
  rows: RecordRow[]
  selectedRow?: number
  onSelect?: (row: number) => void
}) {
  return (
    <table className="w-max min-w-full border-separate border-spacing-0 text-xs">
      <thead className="bg-background sticky top-0 z-10">
        <tr>
          <th className="text-muted-foreground border-b px-2 py-1.5 text-right font-medium">#</th>
          {columns.map((column) => (
            <th key={column} className="border-b px-2 py-1.5 text-left font-medium">
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map(({ row, record }) => {
          const selected = row === selectedRow
          return (
            <tr
              key={row}
              data-row={row}
              aria-selected={selected}
              onClick={() => onSelect?.(row)}
              className={`cursor-default ${selected ? 'bg-accent' : 'hover:bg-accent/50'}`}
            >
              <td className="text-muted-foreground border-b px-2 py-1 text-right tabular-nums">
                {row}
              </td>
              {columns.map((column) => (
                <td
                  key={column}
                  className="max-w-80 truncate border-b px-2 py-1"
                  title={cellTitle(record[column])}
                >
                  <CellValue value={record[column]} />
                </td>
              ))}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** Previous/next and "go to row" for a paged table. */
export function Pager({
  offset,
  total,
  onOffset,
}: {
  offset: number
  total: number
  onOffset: (offset: number) => void
}) {
  const [goto, setGoto] = useState('')
  const last = Math.max(0, total - 1)
  const end = Math.min(offset + PAGE_SIZE, total)
  return (
    <div className="flex items-center gap-1 text-xs">
      <span className="text-muted-foreground tabular-nums">
        {total === 0 ? 'no rows' : `rows ${offset}–${end - 1} of ${total.toLocaleString()}`}
      </span>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Previous page"
        disabled={offset === 0}
        onClick={() => onOffset(Math.max(0, offset - PAGE_SIZE))}
      >
        <ChevronLeftIcon />
      </Button>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Next page"
        disabled={end >= total}
        onClick={() => onOffset(offset + PAGE_SIZE)}
      >
        <ChevronRightIcon />
      </Button>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          const row = Number.parseInt(goto, 10)
          if (Number.isFinite(row)) onOffset(pageStart(Math.min(Math.max(row, 0), last)))
          setGoto('')
        }}
      >
        <input
          aria-label="Go to row"
          placeholder="row"
          inputMode="numeric"
          value={goto}
          onChange={(e) => setGoto(e.target.value)}
          className="bg-background h-6 w-16 rounded-md border px-1.5"
        />
      </form>
    </div>
  )
}

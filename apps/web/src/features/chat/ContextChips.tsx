import { BookOpenTextIcon, FileTextIcon, XIcon } from 'lucide-react'

import { useChatContext } from '@/lib/chatContext'

/** What waits to go out with the next message: selections from a PDF page, entries as shown. */
export function ContextChips() {
  const chips = useChatContext((s) => s.chips)
  const remove = useChatContext((s) => s.remove)
  if (chips.length === 0) return null
  return (
    <ul className="flex flex-wrap gap-1 px-2 pt-1" data-testid="context-chips">
      {chips.map((chip) => {
        const Icon = chip.kind === 'entry' ? BookOpenTextIcon : FileTextIcon
        const noun = chip.kind === 'entry' ? 'entry' : 'selection'
        return (
          <li
            key={chip.id}
            className="bg-muted text-muted-foreground flex max-w-full items-center gap-1 rounded-md px-2 py-0.5 text-xs"
            title={chip.text}
          >
            <Icon className="size-3 shrink-0" />
            <span className="truncate">
              {chip.label}: {chip.text.slice(0, 60)}
              {chip.text.length > 60 ? '…' : ''}
            </span>
            <button
              type="button"
              aria-label={`Remove ${noun} ${chip.label}`}
              onClick={() => remove(chip.id)}
              className="hover:text-foreground"
            >
              <XIcon className="size-3" />
            </button>
          </li>
        )
      })}
    </ul>
  )
}

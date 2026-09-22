import { useState } from 'react'

const VIEWING = '[Looking at: '
const ATTACHED = /^\[(Attached|Selected on page)[^\]]*\]/

/**
 * Text parts of a user message. The lexicographer's own words render as they are; the parts
 * the server appended (the "looking at" note, attached entries and selections) are shown as
 * small notes: the note in one muted line, an attachment as a collapsed block.
 */
export function UserTextPart({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  if (text.startsWith(VIEWING) && text.endsWith(']')) {
    return (
      <p className="text-muted-foreground mt-1 text-xs" data-testid="viewing-note">
        Looking at: {text.slice(VIEWING.length, -1)}
      </p>
    )
  }
  const attached = text.match(ATTACHED)
  if (attached) {
    const head = attached[0].slice(1, -1)
    return (
      <div className="mt-1 text-xs" data-testid="attachment-note">
        <button
          type="button"
          className="text-muted-foreground underline-offset-2 hover:underline"
          onClick={() => setOpen((v) => !v)}
        >
          {head} {open ? '▾' : '▸'}
        </button>
        {open && (
          <pre className="bg-background/60 mt-1 max-h-60 overflow-auto rounded-md p-2 font-mono text-[11px] leading-snug whitespace-pre-wrap">
            {text.slice(attached[0].length).trim()}
          </pre>
        )}
      </div>
    )
  }
  return <p className="whitespace-pre-wrap">{text}</p>
}

const VIEWING = '[Looking at: '

/**
 * Text parts of a user message. The user's own words render as they are; the note the server
 * appends (what was open in the middle pane) is shown as one muted line.
 */
export function UserTextPart({ text }: { text: string }) {
  if (text.startsWith(VIEWING) && text.endsWith(']')) {
    return (
      <p className="text-muted-foreground mt-1 text-xs" data-testid="viewing-note">
        Looking at: {text.slice(VIEWING.length, -1)}
      </p>
    )
  }
  return <p className="whitespace-pre-wrap">{text}</p>
}

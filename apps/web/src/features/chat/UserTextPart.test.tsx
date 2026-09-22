import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { UserTextPart } from '@/features/chat/UserTextPart'

describe('UserTextPart', () => {
  it('renders the words as they are, the note as a muted line, attachments collapsed', () => {
    render(<UserTextPart text="is this entry right?" />)
    expect(screen.getByText('is this entry right?')).toBeInTheDocument()
    render(<UserTextPart text="[Looking at: entry 8 “babble” of oxford.xml]" />)
    expect(screen.getByTestId('viewing-note')).toHaveTextContent(
      'Looking at: entry 8 “babble” of oxford.xml',
    )
    render(<UserTextPart text={'[Attached: entry 8 “babble”]\nSource XML:\n"""\n<se/>\n"""'} />)
    const toggle = screen.getByRole('button', { name: /Attached: entry 8/ })
    expect(screen.queryByText(/<se\/>/)).not.toBeInTheDocument()
    fireEvent.click(toggle)
    expect(screen.getByText(/<se\/>/)).toBeInTheDocument()
  })
})

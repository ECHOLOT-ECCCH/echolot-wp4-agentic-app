import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { UserTextPart } from '@/features/chat/UserTextPart'

describe('UserTextPart', () => {
  it('renders the words as they are and the viewing note as a muted line', () => {
    render(<UserTextPart text="is this row right?" />)
    expect(screen.getByText('is this row right?')).toBeInTheDocument()
    render(<UserTextPart text="[Looking at: row 3 of the file people.csv (file_id f1)]" />)
    expect(screen.getByTestId('viewing-note')).toHaveTextContent(
      'Looking at: row 3 of the file people.csv (file_id f1)',
    )
  })
})

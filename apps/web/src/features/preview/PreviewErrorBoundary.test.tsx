import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PreviewErrorBoundary } from '@/features/preview/PreviewErrorBoundary'
import { renderWithProviders } from '@/test/utils'
import { usePreview } from '@/lib/uiActions'

function Broken({ explode }: { explode: boolean }) {
  if (explode) throw new Error('value must be typeof string but got object')
  return <p>fine</p>
}

afterEach(() => vi.restoreAllMocks())

describe('PreviewErrorBoundary', () => {
  it('shows the error with a way out instead of taking the page down', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    usePreview.getState().openFile('f1')
    const view = renderWithProviders(
      <PreviewErrorBoundary>
        <Broken explode />
      </PreviewErrorBoundary>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('value must be typeof string')
    fireEvent.click(screen.getByRole('button', { name: 'Close preview' }))
    expect(usePreview.getState().target).toEqual({ kind: 'none' })
    view.rerender(
      <PreviewErrorBoundary>
        <Broken explode={false} />
      </PreviewErrorBoundary>,
    )
    expect(screen.getByText('fine')).toBeInTheDocument()
  })
})

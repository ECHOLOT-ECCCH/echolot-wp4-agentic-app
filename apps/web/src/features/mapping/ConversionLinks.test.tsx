import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ConversionLinks } from '@/features/mapping/ConversionLinks'
import { mockApi, renderWithProviders } from '@/test/utils'
import { usePreview } from '@/lib/uiActions'

afterEach(() => {
  vi.unstubAllGlobals()
  usePreview.getState().clear()
})

describe('ConversionLinks', () => {
  it('shows the structure profile instead of a mapping for a PDF project', async () => {
    mockApi([
      {
        path: '/api/projects/p1/structure-profile',
        body: {
          source_language: 'en',
          target_languages: ['sl', 'de'],
          contents: 'x',
          entry_id_prefix: 'e',
          stored: true,
        },
      },
    ])
    renderWithProviders(<ConversionLinks projectId="p1" sourceType="pdf" />)
    expect(screen.getByText('Structuring')).toBeInTheDocument()
    expect(await screen.findByText('en → sl, de')).toBeInTheDocument()
    expect(screen.queryByText('Mapping')).not.toBeInTheDocument()
    expect(screen.getByText('Specification')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Profile/ }))
    expect(usePreview.getState().target).toEqual({ kind: 'profile' })
  })

  it('keeps the mapping for an XML project', async () => {
    mockApi([{ path: '/api/projects/p1/mappings', body: [{ version: 2 }, { version: 1 }] }])
    renderWithProviders(<ConversionLinks projectId="p1" sourceType="xml" />)
    expect(screen.getByText('Conversion')).toBeInTheDocument()
    expect(await screen.findByText('v2 · 2 versions')).toBeInTheDocument()
    expect(screen.queryByText('Profile')).not.toBeInTheDocument()
  })
})

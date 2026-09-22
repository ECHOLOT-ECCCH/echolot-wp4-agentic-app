import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ProfileEditor } from '@/features/pdf/ProfileEditor'
import { fromDraft } from '@/features/pdf/profileDraft'
import { mockApi, renderWithProviders } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

describe('ProfileEditor', () => {
  it('shows the stored profile and saves the edited one', async () => {
    const stored = {
      source_language: 'en',
      target_languages: ['sl'],
      contents: 'Bold headwords.',
      entry_id_prefix: 'e',
      stored: true,
    }
    const { fetchMock } = mockApi([
      {
        method: 'PUT',
        path: '/api/projects/p1/structure-profile',
        body: { ...stored, contents: 'Bold headwords, numbered senses.' },
      },
      { path: '/api/projects/p1/structure-profile', body: stored },
    ])
    renderWithProviders(<ProfileEditor projectId="p1" canEdit />)
    expect(await screen.findByText('stored with the project')).toBeInTheDocument()
    expect(screen.getByLabelText('Source language')).toHaveValue('en')
    expect(screen.getByLabelText(/Target languages/)).toHaveValue('sl')
    const save = screen.getByRole('button', { name: /Save/ })
    expect(save).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/What an entry contains/), {
      target: { value: 'Bold headwords, numbered senses.' },
    })
    fireEvent.change(screen.getByLabelText(/Target languages/), { target: { value: 'sl, de ' } })
    expect(screen.getByText('unsaved changes')).toBeInTheDocument()
    fireEvent.click(save)
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === 'PUT',
      )
      expect(put).toBeDefined()
      expect(JSON.parse(String((put![1] as RequestInit).body))).toEqual({
        source_language: 'en',
        target_languages: ['sl', 'de'],
        contents: 'Bold headwords, numbered senses.',
        entry_id_prefix: 'e',
      })
    })
  })

  it('reads an unset profile as defaults', () => {
    expect(
      fromDraft({ source_language: ' ', target_languages: '', contents: '', entry_id_prefix: '' }),
    ).toEqual({
      source_language: 'unknown',
      target_languages: [],
      contents: '',
      entry_id_prefix: 'e',
    })
  })
})

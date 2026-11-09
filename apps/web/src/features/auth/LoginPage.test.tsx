import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LoginPage } from '@/features/auth/LoginPage'
import { mockApi, renderWithProviders } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

describe('LoginPage', () => {
  it('sends the credentials and shows a refusal', async () => {
    const { calls } = mockApi([
      {
        method: 'POST',
        path: '/api/auth/login',
        status: 401,
        body: { detail: 'Wrong e-mail address or password' },
      },
    ])
    renderWithProviders(<LoginPage />)
    fireEvent.change(screen.getByLabelText('E-mail'), { target: { value: ' ana@example.org ' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'not it' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong e-mail address or password')
    expect(calls[0]?.body).toEqual({ email: 'ana@example.org', password: 'not it' })
  })
})

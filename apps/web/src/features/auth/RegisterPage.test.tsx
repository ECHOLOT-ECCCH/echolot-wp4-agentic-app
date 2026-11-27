import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ConfirmEmailPage } from '@/features/auth/ConfirmEmailPage'
import { LoginPage } from '@/features/auth/LoginPage'
import { RegisterPage } from '@/features/auth/RegisterPage'
import { mockApi, renderWithProviders } from '@/test/utils'

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
})

const open = {
  path: '/api/auth/registration',
  body: { enabled: true, reviewer: 'An administrator at the Institute' },
}

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

describe('RegisterPage', () => {
  it('sends a request and says to check the inbox', async () => {
    const { calls } = mockApi([
      open,
      { method: 'POST', path: '/api/auth/register', status: 202, body: { status: 'check_email' } },
    ])
    renderWithProviders(<RegisterPage />)
    await screen.findByLabelText('Name')
    fill('Name', ' Maja Novak ')
    fill('Organisation', 'Example Institute')
    fill('E-mail', 'maja@example.org')
    fill('What will you use the workbench for?', 'Reconcile our archive.')
    fill('Password', 'a long enough passphrase')
    fill('Password again', 'a long enough passphrasE')
    expect(screen.getByText('The passwords differ.')).toBeInTheDocument()
    const send = screen.getByRole('button', { name: 'Request an account' })
    expect(send).toBeDisabled()
    fill('Password again', 'a long enough passphrase')
    fireEvent.click(send)

    expect(await screen.findByText('Check your inbox')).toBeInTheDocument()
    expect(screen.getByText('maja@example.org')).toBeInTheDocument()
    expect(
      screen.getByText(/An administrator at the Institute reviews each request/),
    ).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      email: 'maja@example.org',
      name: 'Maja Novak',
      organisation: 'Example Institute',
      purpose: 'Reconcile our archive.',
      password: 'a long enough passphrase',
      website: '',
    })
  })

  it('shows why a request was refused', async () => {
    mockApi([
      open,
      {
        method: 'POST',
        path: '/api/auth/register',
        status: 429,
        body: { detail: 'Too many requests for an account. Try again later.' },
      },
    ])
    renderWithProviders(<RegisterPage />)
    await screen.findByLabelText('Name')
    fill('Name', 'Maja')
    fill('Organisation', 'Example')
    fill('E-mail', 'maja@example.org')
    fill('What will you use the workbench for?', 'Work.')
    fill('Password', 'a long enough passphrase')
    fill('Password again', 'a long enough passphrase')
    fireEvent.click(screen.getByRole('button', { name: 'Request an account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many requests')
  })

  it('says so when this server takes no requests', async () => {
    mockApi([{ path: '/api/auth/registration', body: { enabled: false, reviewer: 'x' } }])
    renderWithProviders(<RegisterPage />)
    expect(await screen.findByText(/does not take requests for accounts/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument()
  })
})

describe('ConfirmEmailPage', () => {
  it('confirms the address on a click, not on load', async () => {
    window.history.replaceState(null, '', '/confirm-email?token=abc123abc123abc123')
    const { calls } = mockApi([
      open,
      { method: 'POST', path: '/api/auth/confirm-email', body: { status: 'pending' } },
    ])
    renderWithProviders(<ConfirmEmailPage />)
    const button = await screen.findByRole('button', { name: 'Confirm my address' })
    expect(calls.some((c) => c.method === 'POST')).toBe(false)
    fireEvent.click(button)
    expect(await screen.findByText('Your address is confirmed')).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ token: 'abc123abc123abc123' })
  })

  it('shows why a link does not work', async () => {
    window.history.replaceState(null, '', '/confirm-email?token=abc123abc123abc123')
    mockApi([
      open,
      {
        method: 'POST',
        path: '/api/auth/confirm-email',
        status: 400,
        body: { detail: 'This link does not work any more' },
      },
    ])
    renderWithProviders(<ConfirmEmailPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm my address' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('does not work any more')
  })

  it('explains a link without its token', () => {
    window.history.replaceState(null, '', '/confirm-email')
    mockApi([open])
    renderWithProviders(<ConfirmEmailPage />)
    expect(screen.getByText(/This link is incomplete/)).toBeInTheDocument()
  })
})

describe('LoginPage and registration', () => {
  it('offers a request for an account when the server takes them', async () => {
    mockApi([open])
    renderWithProviders(<LoginPage />)
    expect(await screen.findByRole('link', { name: 'Request an account' })).toHaveAttribute(
      'href',
      '/register',
    )
  })

  it('tells people to ask otherwise', async () => {
    mockApi([{ path: '/api/auth/registration', body: { enabled: false, reviewer: 'x' } }])
    renderWithProviders(<LoginPage />)
    expect(await screen.findByText(/Ask the person who runs this workbench/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Request an account' })).not.toBeInTheDocument()
  })
})

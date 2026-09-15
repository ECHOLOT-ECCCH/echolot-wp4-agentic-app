import { render, screen } from '@testing-library/react'

import { App } from '@/app/App'

describe('App', () => {
  it('renders the home page', async () => {
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Elexifier' })).toBeInTheDocument()
  })
})

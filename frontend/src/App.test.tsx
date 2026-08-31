import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('authentication entrypoints', () => {
  it('renders Discord login without offering public registration', () => {
    render(
      <MemoryRouter initialEntries={['/login']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /continue with discord/i })).toHaveAttribute(
      'href',
      '/api/auth/discord/login',
    )
    expect(screen.getByText(/new accounts require an invitation url/i)).toBeInTheDocument()
  })
})

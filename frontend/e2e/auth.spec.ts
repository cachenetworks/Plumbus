import { expect, test } from '@playwright/test'

test('normal login page never offers public registration', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await expect(page.getByText(/New accounts require an invitation URL/i)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Continue with Discord' })).toHaveAttribute('href', '/api/auth/discord/login')
  await expect(page.getByRole('link', { name: /register|sign up/i })).toHaveCount(0)
})

test('invite route validates token before presenting Discord registration', async ({ page }) => {
  await page.route('**/api/invites/secure-test-token/status', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        valid: true,
        assigned_role: 'Member',
        continue_url: '/api/auth/discord/register/secure-test-token',
      }),
    })
  })

  await page.goto('/invite/secure-test-token')
  await expect(page.getByRole('heading', { name: 'Join Plumbus' })).toBeVisible()
  await expect(page.getByText(/invited to join as/i)).toContainText('Member')
  await expect(page.getByRole('link', { name: 'Continue with Discord' })).toHaveAttribute(
    'href',
    '/api/auth/discord/register/secure-test-token',
  )
})

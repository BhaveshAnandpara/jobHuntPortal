/**
 * Shell/navigation behavior for `<Layout>` — added with T3
 * (docs/frontend/frontend-revamp-spec.md), which restyled the shell and is
 * the first ticket to assert its structure rather than only reaching it
 * incidentally through `App.smoke.test.tsx`.
 *
 * What's covered here is the part that would silently rot: the nav offering
 * exactly the real routes in `router.tsx` (no invented destinations), the
 * current route being marked as such, the single sign-out affordance
 * actually ending the session, the mobile collapse exposing the same set of
 * destinations rather than a reduced one, and the skip link.
 *
 * Owner: frontend-shell-agent.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { Layout } from './Layout'
import { consumeIntentionalSignOut } from './sessionNotice'
import { IdentityProvider } from '../hooks/IdentityProvider'
import { getToken, setToken } from '../hooks/identity'
import { mintTestToken } from '../../tests/support/jwt'

/**
 * The five top-level destinations in `router.tsx`. Kept here as a literal so
 * a nav item added without a matching route (or a route quietly dropped from
 * the nav) fails this test rather than shipping.
 */
const EXPECTED_DESTINATIONS = [
  { label: 'Dashboard', href: '/' },
  { label: 'Resumes', href: '/resumes' },
  { label: 'Opportunities', href: '/opportunities' },
  { label: 'Outreach', href: '/outreach' },
  { label: 'Settings', href: '/settings' },
]

function renderLayout(initialPath = '/') {
  setToken(mintTestToken('user-123'))
  return render(
    <IdentityProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/login" element={<p>Login Page</p>} />
          <Route element={<Layout />}>
            <Route path="/" element={<p>Dashboard content</p>} />
            <Route path="/resumes" element={<p>Resumes content</p>} />
            <Route path="/settings" element={<p>Settings content</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </IdentityProvider>,
  )
}

afterEach(() => {
  // Module-scoped one-shot flag — drain it so a sign-out in one test can't
  // leak into the next.
  consumeIntentionalSignOut()
})

describe('Layout navigation', () => {
  it('offers exactly the five real top-level routes, and nothing invented', () => {
    renderLayout()

    const links = within(screen.getByRole('navigation')).getAllByRole('link')
    expect(links).toHaveLength(EXPECTED_DESTINATIONS.length)
    EXPECTED_DESTINATIONS.forEach(({ label, href }, index) => {
      expect(links[index]).toHaveAccessibleName(label)
      expect(links[index]).toHaveAttribute('href', href)
    })
  })

  it('marks the current route as the current page for assistive tech', () => {
    renderLayout('/settings')

    const nav = within(screen.getByRole('navigation'))
    expect(nav.getByRole('link', { name: 'Settings' })).toHaveAttribute('aria-current', 'page')
    expect(nav.getByRole('link', { name: 'Dashboard' })).not.toHaveAttribute('aria-current')
  })

  it('renders the routed page inside the main content region the skip link targets', () => {
    const { container } = renderLayout()

    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute(
      'href',
      '#main-content',
    )
    const main = container.querySelector('#main-content')
    expect(main).not.toBeNull()
    expect(within(main as HTMLElement).getByText('Dashboard content')).toBeInTheDocument()
  })
})

describe('Layout sign-out', () => {
  it('ends the session and returns to /login', async () => {
    renderLayout()

    await userEvent.click(screen.getByRole('button', { name: 'Log out' }))

    expect(getToken()).toBeNull()
    expect(screen.getByText('Login Page')).toBeInTheDocument()
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
  })

  it('records the sign-out as deliberate so the route guard stays quiet', async () => {
    renderLayout()

    await userEvent.click(screen.getByRole('button', { name: 'Log out' }))

    // `RequireIdentity` reads this to suppress its "Please sign in to
    // continue" notice — leaving is what the user just asked for.
    expect(consumeIntentionalSignOut()).toBe(true)
  })
})

describe('Layout mobile collapse', () => {
  it('opens the nav menu from the hamburger toggle', async () => {
    renderLayout()

    const toggle = screen.getByRole('button', { name: 'Open navigation menu' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    await userEvent.click(toggle)

    expect(await screen.findByRole('dialog', { name: 'Menu' })).toBeInTheDocument()
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })

  it('exposes the same destinations and sign-out as the desktop sidebar', async () => {
    renderLayout()

    await userEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }))
    const menu = within(await screen.findByRole('dialog', { name: 'Menu' }))

    EXPECTED_DESTINATIONS.forEach(({ label, href }) => {
      expect(menu.getByRole('link', { name: label })).toHaveAttribute('href', href)
    })
    expect(menu.getByRole('button', { name: 'Log out' })).toBeInTheDocument()
  })

  it('closes itself after navigating, rather than covering the page it just opened', async () => {
    renderLayout()

    await userEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }))
    const menu = await screen.findByRole('dialog', { name: 'Menu' })
    await userEvent.click(within(menu).getByRole('link', { name: 'Resumes' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByText('Resumes content')).toBeInTheDocument()
  })
})

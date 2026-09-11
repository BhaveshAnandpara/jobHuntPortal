/**
 * App shell: nav + content slot. Route content renders via `<Outlet />`.
 *
 * The nav's link set is exactly the five top-level destinations in
 * `router.tsx` — Dashboard, Resumes, Opportunities, Outreach, Settings. The
 * two detail routes (`/opportunities/:applicationId`,
 * `/outreach/:outreachId`) are reached from their list pages and are not nav
 * destinations; nothing else is invented here.
 *
 * Desktop (`md:` and up): a persistent left sidebar.
 * Below `md:` (~768px, the breakpoint documented in
 * docs/frontend/design-system.md#responsive-behavior): a sticky top bar
 * (wordmark + hamburger toggle) that opens the same nav list inside
 * `components/Dialog`, reusing the existing modal primitive rather than
 * introducing a drawer/sheet. A sheet would be the better mobile pattern;
 * adding one means a new shared primitive in `src/components`, which is
 * outside this ticket's file scope — noted, not silently forgotten.
 *
 * T3 (docs/frontend/frontend-revamp-spec.md): restyled onto the shadcn
 * surface tokens (`bg-background`, `bg-muted`, `border-border`,
 * `text-muted-foreground`, `ring-ring`) with the active nav state using the
 * documented, WCAG-AA-verified in-progress status pair from src/index.css
 * (`--color-status-progress` on `--color-status-progress-bg`, 6.16:1) rather
 * than a one-off color. A skip-to-content link was added ahead of the nav so
 * keyboard users aren't forced through six shell controls on every page.
 *
 * Owner: frontend-shell-agent.
 */

import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { Briefcase, FileText, LayoutDashboard, LogOut, Menu, Send, Settings } from 'lucide-react'
import { Button, Dialog } from '../components'
import { useCurrentUserId } from '../hooks/identity'
import { markIntentionalSignOut } from './sessionNotice'
import { cn } from '@/lib/utils'

const APP_NAME = 'Career Platform'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/resumes', label: 'Resumes', icon: FileText, end: false },
  { to: '/opportunities', label: 'Opportunities', icon: Briefcase, end: false },
  { to: '/outreach', label: 'Outreach', icon: Send, end: false },
  { to: '/settings', label: 'Settings', icon: Settings, end: false },
] as const

/**
 * The focus ring is *added to* the browser's default outline rather than
 * replacing it (no `outline-none` here) — `tests/e2e/responsive-accessibility.spec.ts`
 * asserts a real, computed focus indicator on these links, and suppressing
 * the native outline in favor of a ring that a headless browser may not
 * consider `:focus-visible` would be a regression dressed up as polish.
 */
const NAV_LINK_BASE =
  'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring'

function NavLinkList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <ul className="space-y-1">
      {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
        <li key={to}>
          <NavLink
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                NAV_LINK_BASE,
                isActive
                  ? 'bg-status-progress-bg text-status-progress'
                  : 'text-gray-600 hover:bg-muted hover:text-gray-900',
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" aria-hidden />
            {label}
          </NavLink>
        </li>
      ))}
    </ul>
  )
}

/**
 * The only sign-out affordance in the app. `clearToken()` (from
 * `IdentityContext`) both drops the persisted JWT and clears the React Query
 * cache, so no previous account's data survives into the next session; the
 * explicit `navigate('/login')` just makes the transition immediate instead
 * of waiting on `RequireIdentity`'s own redirect.
 *
 * `markIntentionalSignOut()` runs first so the guard knows this bounce was
 * requested and skips its "Please sign in to continue" notice.
 */
function LogoutButton({ onNavigate }: { onNavigate?: () => void }) {
  const { clearToken } = useCurrentUserId()
  const navigate = useNavigate()

  return (
    <Button
      variant="secondary"
      onClick={() => {
        onNavigate?.()
        markIntentionalSignOut()
        clearToken()
        navigate('/login', { replace: true })
      }}
      className="w-full justify-start gap-3 border-transparent bg-transparent px-3 text-gray-600 shadow-none hover:bg-muted hover:text-gray-900"
    >
      <LogOut className="h-4 w-4 shrink-0" aria-hidden />
      Log out
    </Button>
  )
}

export function Layout() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  return (
    <div className="min-h-screen bg-muted/40">
      <a
        href="#main-content"
        className="sr-only rounded-md bg-background px-4 py-2 text-sm font-medium text-gray-900 ring-2 ring-ring focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50"
      >
        Skip to content
      </a>

      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-border bg-background/95 px-4 py-3 backdrop-blur md:hidden">
        <p className="text-sm font-semibold tracking-tight text-gray-900">{APP_NAME}</p>
        <button
          type="button"
          onClick={() => setMobileNavOpen(true)}
          aria-label="Open navigation menu"
          aria-haspopup="dialog"
          aria-expanded={mobileNavOpen}
          className="rounded-md p-2 text-gray-600 hover:bg-muted hover:text-gray-900 focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Menu className="h-5 w-5" aria-hidden />
        </button>
      </header>

      <Dialog
        open={mobileNavOpen}
        onOpenChange={setMobileNavOpen}
        title="Menu"
        description={`Go to a section of ${APP_NAME}, or sign out.`}
      >
        <NavLinkList onNavigate={() => setMobileNavOpen(false)} />
        <div className="mt-4 border-t border-border pt-4">
          <LogoutButton onNavigate={() => setMobileNavOpen(false)} />
        </div>
      </Dialog>

      <div className="flex min-h-screen">
        <nav
          aria-label="Primary"
          className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-border bg-background px-3 py-6 md:flex"
        >
          <p className="px-3 pb-6 text-sm font-semibold tracking-tight text-gray-900">{APP_NAME}</p>
          <NavLinkList />
          <div className="mt-auto border-t border-border pt-4">
            <LogoutButton />
          </div>
        </nav>
        <main id="main-content" className="min-w-0 flex-1 px-6 py-8 md:px-10">
          <div className="mx-auto w-full max-w-5xl">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}

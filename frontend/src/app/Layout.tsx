/**
 * App shell: nav + content slot. Route content renders via `<Outlet />`.
 * See docs/frontend/routes.md's route table for the nav's exact link set —
 * `/welcome` is intentionally not a nav item (it's a one-time onboarding
 * destination, not a page a returning user navigates back to).
 *
 * Desktop (`md:` and up): a persistent left sidebar.
 * Below `md:`: a sticky top bar (branding + hamburger toggle) that opens
 * the nav list inside `components/Dialog` — reusing frontend-design-agent's
 * existing modal primitive rather than introducing a new drawer/sheet
 * component, since none exists yet in `src/components`. If a slide-in
 * drawer primitive is added later, this can switch to it; a centered modal
 * listing the same five destinations is a reasonable interim mobile nav
 * pattern, not a placeholder that leaves the surface unusable.
 *
 * Owner: frontend-shell-agent.
 */

import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { Briefcase, FileText, LayoutDashboard, Menu, Send, Settings } from 'lucide-react'
import { Dialog } from '../components'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/resumes', label: 'Resumes', icon: FileText, end: false },
  { to: '/opportunities', label: 'Opportunities', icon: Briefcase, end: false },
  { to: '/outreach', label: 'Outreach', icon: Send, end: false },
  { to: '/settings', label: 'Settings', icon: Settings, end: false },
] as const

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
              `flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium ${
                isActive
                  ? 'bg-status-progress-bg text-brand'
                  : 'text-gray-600 hover:bg-gray-100'
              }`
            }
          >
            <Icon className="h-4 w-4" aria-hidden />
            {label}
          </NavLink>
        </li>
      ))}
    </ul>
  )
}

export function Layout() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="sticky top-0 z-10 flex items-center justify-between border-b border-gray-200 bg-white px-4 py-3 md:hidden">
        <p className="text-sm font-semibold text-gray-900">Career Platform</p>
        <button
          type="button"
          onClick={() => setMobileNavOpen(true)}
          aria-label="Open navigation menu"
          aria-haspopup="dialog"
          className="rounded-md p-2 text-gray-600 hover:bg-gray-100"
        >
          <Menu className="h-5 w-5" aria-hidden />
        </button>
      </header>

      <Dialog open={mobileNavOpen} onOpenChange={setMobileNavOpen} title="Menu">
        <NavLinkList onNavigate={() => setMobileNavOpen(false)} />
      </Dialog>

      <div className="mx-auto flex max-w-6xl">
        <nav className="sticky top-0 hidden h-screen w-56 shrink-0 border-r border-gray-200 bg-white px-3 py-6 md:block">
          <p className="px-3 pb-6 text-sm font-semibold text-gray-900">Career Platform</p>
          <NavLinkList />
        </nav>
        <main className="min-w-0 flex-1 px-6 py-8 md:px-10">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

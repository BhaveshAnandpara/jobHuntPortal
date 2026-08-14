/**
 * The route table — see docs/frontend/routes.md's route table for the
 * documented final routes; this must not diverge from it. Every route
 * except `/welcome` sits behind `<RequireIdentity />`.
 *
 * Owner: frontend-shell-agent. Feature agents replace the placeholder
 * page components their route points at; they do not add or rename routes
 * here without an architecture-level reason (routes.md would need to
 * change first).
 */

import type { ReactNode } from 'react'
import { Routes, Route } from 'react-router-dom'
import { Layout } from './Layout'
import { RequireIdentity } from './RequireIdentity'
import { ErrorBoundary } from './ErrorBoundary'
import { WelcomePage } from '../features/identity/WelcomePage'
import { SettingsPage } from '../features/preferences/SettingsPage'
import { ResumesPage } from '../features/resumes/ResumesPage'
import { DashboardPage } from '../features/opportunities/DashboardPage'
import { OpportunitiesPage } from '../features/opportunities/OpportunitiesPage'
import { OpportunityDetailPage } from '../features/opportunities/OpportunityDetailPage'
import { OutreachQueuePage } from '../features/outreach/OutreachQueuePage'
import { OutreachReviewPage } from '../features/outreach/OutreachReviewPage'

function withBoundary(element: ReactNode) {
  return <ErrorBoundary>{element}</ErrorBoundary>
}

export function AppRouter() {
  return (
    <Routes>
      <Route path="/welcome" element={withBoundary(<WelcomePage />)} />

      <Route element={<RequireIdentity />}>
        <Route element={<Layout />}>
          <Route path="/" element={withBoundary(<DashboardPage />)} />
          <Route path="/resumes" element={withBoundary(<ResumesPage />)} />
          <Route path="/opportunities" element={withBoundary(<OpportunitiesPage />)} />
          <Route
            path="/opportunities/:applicationId"
            element={withBoundary(<OpportunityDetailPage />)}
          />
          <Route path="/outreach" element={withBoundary(<OutreachQueuePage />)} />
          <Route path="/outreach/:outreachId" element={withBoundary(<OutreachReviewPage />)} />
          <Route path="/settings" element={withBoundary(<SettingsPage />)} />
        </Route>
      </Route>
    </Routes>
  )
}

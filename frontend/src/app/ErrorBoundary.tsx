/**
 * Last-resort safety net for a genuine render-time exception — see
 * docs/frontend/error-handling.md's "render-time exception" row. Not a
 * designed-for path: API errors never reach here, they're handled by
 * TanStack Query + the per-page/per-panel ErrorState component instead.
 *
 * Owner: frontend-shell-agent. Mounted once per route in `router.tsx` so
 * one page's crash doesn't take down the nav/shell around it.
 *
 * T3 (docs/frontend/frontend-revamp-spec.md): the fallback is a real
 * recovery surface, not a dead end — a shadcn `Card` with an explanation of
 * what happened, a primary "Reload" (the actual fix for a render crash: the
 * component tree is already corrupt, so re-mounting in place is not enough)
 * and a secondary escape hatch back to the dashboard for the case where this
 * particular page keeps failing. It is announced via `role="alert"` so a
 * screen-reader user is told the page changed under them.
 *
 * It deliberately does NOT print the caught error's message: per
 * error-handling.md's "what never happens", raw exception text never reaches
 * the user. The full error and component stack go to the console for the
 * developer instead.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { TriangleAlert } from 'lucide-react'
import { Button, Card } from '../components'

type ErrorBoundaryProps = {
  children: ReactNode
}

type ErrorBoundaryState = {
  hasError: boolean
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error('Unhandled render error', error, info)
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div role="alert" className="flex min-h-[60vh] items-center justify-center px-4 py-12">
          <Card className="w-full max-w-md p-8 text-center">
            <span
              className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-status-negative-bg"
              aria-hidden
            >
              <TriangleAlert className="h-6 w-6 text-status-negative" />
            </span>
            <h2 className="text-xl font-semibold text-gray-900">Something went wrong.</h2>
            <p className="mt-2 text-sm text-gray-600">
              This page hit an unexpected error and stopped rendering. Nothing you saved was lost —
              reloading almost always clears it.
            </p>
            <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Button variant="primary" onClick={() => window.location.reload()}>
                Reload
              </Button>
              {/*
                A plain anchor, not a react-router <Link>: a render crash can
                come from anywhere in the tree, including a page whose state
                is what broke. A full document load to `/` guarantees a clean
                app, which a client-side route change would not.
              */}
              <a
                href="/"
                className="rounded-md px-4 py-2 text-sm font-medium text-gray-600 underline-offset-4 hover:text-gray-900 hover:underline focus-visible:ring-2 focus-visible:ring-ring"
              >
                Back to dashboard
              </a>
            </div>
          </Card>
        </div>
      )
    }
    return this.props.children
  }
}

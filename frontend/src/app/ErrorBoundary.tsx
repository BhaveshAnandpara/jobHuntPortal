/**
 * Last-resort safety net for a genuine render-time exception — see
 * docs/frontend/error-handling.md's "render-time exception" row. Not a
 * designed-for path: API errors never reach here, they're handled by
 * TanStack Query + the per-page/per-panel ErrorState component instead.
 *
 * Owner: frontend-shell-agent. Mounted once per route in `router.tsx` so
 * one page's crash doesn't take down the nav/shell around it.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button } from '../components'

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
        <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
          <p className="text-sm font-medium text-gray-900">Something went wrong.</p>
          <Button variant="secondary" onClick={() => window.location.reload()}>
            Reload
          </Button>
        </div>
      )
    }
    return this.props.children
  }
}

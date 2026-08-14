/**
 * Owner: frontend-design-agent. Radix-backed for focus trap/ARIA — see
 * docs/frontend/architecture.md's stack table. Used for confirmations
 * (e.g. delete resume) and any modal review flow.
 *
 * Input: `open`, `onOpenChange`, `title`, optional `description`, `children`.
 * Output: modal dialog.
 * Consumers: resumes (delete confirm), any future confirm-style modal.
 */

import * as RadixDialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ReactNode, RefObject } from 'react'

type DialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  /**
   * Radix requires every Dialog.Content to have an accessible description
   * (via Dialog.Description or an explicit aria-describedby) or it warns in
   * the console. When the dialog's body is self-explanatory and no visible
   * description copy is needed, this renders a screen-reader-only fallback
   * instead of forcing every caller to supply one.
   */
  description?: string
  children: ReactNode
  /**
   * The element focus should return to once the dialog closes (Escape,
   * Cancel, or any other close path). Only needed because this component
   * is always used as a fully controlled dialog (`open`/`onOpenChange`
   * driven by caller state) rather than via Radix's own `Dialog.Trigger` —
   * with no Trigger, Radix has no element on record to restore focus to on
   * close. Optional and additive: omitting it leaves Radix's own default
   * close-focus behavior untouched, so existing callers are unaffected.
   */
  triggerRef?: RefObject<HTMLElement | null>
}

export function Dialog({ open, onOpenChange, title, description, children, triggerRef }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 bg-black/40" />
        <RadixDialog.Content
          className="fixed top-1/2 left-1/2 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg bg-white p-6 shadow-lg focus:outline-none"
          onCloseAutoFocus={(event) => {
            if (triggerRef?.current) {
              event.preventDefault()
              triggerRef.current.focus()
            }
          }}
        >
          <div className="flex items-center justify-between pb-4">
            <RadixDialog.Title className="text-base font-semibold text-gray-900">
              {title}
            </RadixDialog.Title>
            <RadixDialog.Close
              aria-label="Close"
              className="rounded text-gray-400 hover:text-gray-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
            >
              <X className="h-4 w-4" aria-hidden />
            </RadixDialog.Close>
          </div>
          <RadixDialog.Description className={description ? 'mb-4 text-sm text-gray-500' : 'sr-only'}>
            {description ?? title}
          </RadixDialog.Description>
          {children}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  )
}

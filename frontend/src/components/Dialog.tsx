/**
 * Owner: frontend-design-agent. Radix-backed for focus trap/ARIA — see
 * docs/frontend/architecture.md's stack table. Used for confirmations
 * (e.g. delete resume) and any modal review flow.
 *
 * Input: `open`, `onOpenChange`, `title`, optional `description`, `children`.
 * Output: modal dialog.
 * Consumers: resumes (delete confirm), any future confirm-style modal.
 *
 * T2 (docs/frontend/frontend-revamp-spec.md): internals are now shadcn's
 * `ui/dialog` parts (still Radix underneath, via the unified `radix-ui`
 * package). The exported contract is unchanged — callers pass `title`/
 * `description` as strings rather than composing `<DialogHeader>` children,
 * and the close affordance is shadcn's built-in `showCloseButton` control,
 * which carries the same "Close" accessible name as before.
 */

import { X } from 'lucide-react'
import type { ReactNode, RefObject } from 'react'
import {
  Dialog as ShadcnDialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from './ui/dialog'

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
    <ShadcnDialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        // shadcn caps content at `sm:max-w-sm`; this app's confirm dialogs
        // were built at `max-w-md` and their copy is laid out for it.
        className="gap-0 rounded-lg bg-white p-6 sm:max-w-md"
        showCloseButton={false}
        onCloseAutoFocus={(event) => {
          if (triggerRef?.current) {
            event.preventDefault()
            triggerRef.current.focus()
          }
        }}
      >
        <DialogHeader className="flex-row items-center justify-between pb-4">
          <DialogTitle className="text-base font-semibold text-gray-900">{title}</DialogTitle>
          {/*
            shadcn's own `showCloseButton` renders an absolutely positioned
            close in the content's top-right corner. This dialog has always
            put the close in the header row next to the title, so
            `showCloseButton` is off above and the same Radix `Dialog.Close`
            is rendered inline here — identical behavior and accessible name,
            existing placement.
          */}
          <DialogClose
            aria-label="Close"
            className="rounded text-gray-400 hover:text-gray-600 focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
          >
            <X className="h-4 w-4" aria-hidden />
          </DialogClose>
        </DialogHeader>
        <DialogDescription className={description ? 'mb-4 text-sm text-gray-500' : 'sr-only'}>
          {description ?? title}
        </DialogDescription>
        {children}
      </DialogContent>
    </ShadcnDialog>
  )
}

/**
 * The one rendering of "you have no AI provider key yet" — used by both
 * gated actions (job submission and resume upload) so the two can't drift
 * into two different explanations of the same blocker. Lives beside
 * `llmConfigReadiness.ts` for the same cross-feature reason.
 *
 * No new visual language: this is the shared `EmptyState` with a CTA, the
 * exact pattern the "no analyzed resume" onboarding state already uses on
 * the Dashboard and Opportunities pages — only the icon, the copy and the
 * destination (`/settings`, not `/resumes`) differ. The compact `Notice`
 * variant likewise mirrors the inline treatment `JobUrlSubmitForm` already
 * uses in place of its input, where a full empty state inside the primary
 * action card would be much heavier than the state it replaces.
 */

import { Link } from 'react-router-dom'
import { KeyRound } from 'lucide-react'
import { EmptyState } from '../../components'

/**
 * Mirrors the shared `Button`'s primary treatment for a link that has to be
 * a real `<a>` (it navigates). Same constant, and same reasoning, as
 * `DashboardPage.tsx`/`OpportunitiesPage.tsx`: `Button` always renders a
 * `<button>`, and adding an `asChild`/`href` escape hatch to it would be a
 * change to `src/components`.
 */
const PRIMARY_LINK_CLASSES =
  'inline-flex items-center gap-2 rounded-md bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-hover focus-visible:ring-2 focus-visible:ring-ring'

const LLM_CONFIG_REQUIRED_TITLE = 'Add your AI provider key in Settings'

const LLM_CONFIG_CTA_LABEL = 'Go to Settings'

/** The compact variant's link text. Deliberately *not* the same string as
 * the title above: both can be on screen at once (Opportunities renders the
 * gated form and the gated empty state together), and two identical labels
 * pointing at the same place read as a duplication bug. */
const LLM_CONFIG_INLINE_CTA_LABEL = 'Add your key in Settings'

type LlmConfigRequiredEmptyStateProps = {
  /** What the missing key is blocking, so the copy names the actual action
   * the user was about to take rather than "AI features" in the abstract. */
  description: string
}

export function LlmConfigRequiredEmptyState({ description }: LlmConfigRequiredEmptyStateProps) {
  return (
    <EmptyState
      icon={<KeyRound className="h-8 w-8" aria-hidden />}
      title={LLM_CONFIG_REQUIRED_TITLE}
      description={description}
      action={
        <Link to="/settings" className={PRIMARY_LINK_CLASSES}>
          {LLM_CONFIG_CTA_LABEL}
        </Link>
      }
    />
  )
}

/** The compact variant, for slots too small for a full empty state. */
export function LlmConfigRequiredNotice({ description }: LlmConfigRequiredEmptyStateProps) {
  return (
    <p className="text-sm text-gray-500">
      {description}{' '}
      <Link to="/settings" className="font-medium text-brand hover:underline">
        {LLM_CONFIG_INLINE_CTA_LABEL}
      </Link>
    </p>
  )
}

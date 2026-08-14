# Visual Design Direction

## Feel

A modern productivity tool (think a clean issue tracker or ATS), not an
"AI demo." Concretely:

- **Prioritize:** clarity, generous whitespace, a clear type scale (one
  display size, one heading size, one body size, one small/meta size —
  not five), obvious primary actions (one filled-button primary action per
  screen — e.g. "Analyze Job" on the Dashboard, "Approve" in the review
  panel), consistent status badges (see below), accessible contrast (WCAG
  AA minimum on every badge/text combination — status colors especially,
  since badges carry meaning by color).
- **Avoid:** gradients, glow/blur effects, animated "thinking" indicators
  beyond a plain spinner, sparkle/chat-bubble AI iconography, chart-heavy
  dashboards (per brief section 13 — the Dashboard is action-first, not an
  analytics surface), dense data-grid aesthetics borrowed from generic
  admin templates.

## Status badges

Every lifecycle enum in this system (`ResumeStatus`, `ApplicationStatus`,
`OutreachStatus`, `ContactStatus`, `JobProcessingStatus`) renders through
one shared `<StatusBadge>` component with a fixed color mapping by
*category*, not by individual value — so the palette stays small and
learnable rather than growing one color per enum member:

| Category | Color | Applies to |
|---|---|---|
| In progress | Blue, subtle pulse (not a spinner) | `PARSING`, `DISCOVERED`, `MATCHED`, `CONTACT_SEARCH`, `NORMALIZED` |
| Needs your attention | Amber | `PENDING_APPROVAL`, `EDITED` |
| Positive / advanced | Green | `PARSED`, `SHORTLISTED`, `CONTACT_FOUND`, `OUTREACH_GENERATED`, `APPROVED`, `SENT`, `RANKED`, `OFFER` |
| Negative / stopped | Red | `PARSE_FAILED`, `IGNORED`, `REJECTED`, `SEND_FAILED`, `FAILED` |
| Neutral / archived | Gray | `ARCHIVED`, `WITHDRAWN` |

This mapping is a UI-only concern — it does not encode business meaning
beyond "how urgent/positive does this look," and adding a new enum member
on the backend later only requires adding one row to this table, not new
component logic.

## Primary action emphasis

Exactly one filled/primary-styled button visible per screen at a time:
"Analyze Job" (Dashboard), "Upload" (Resumes), "Approve" (Outreach Review
— "Reject" and "Edit" are secondary-styled, not equally weighted, since
approval is the action requiring the least friction per brief section 11
while reject/edit remain fully available, not hidden). This directly
supports brief section 25's "obvious primary actions" and "clear approval
actions."

## Responsive behavior

Desktop-first, not a separate mobile app (brief section 20). Concretely:

- `OpportunityTable` (desktop: full table) collapses to a stacked card list
  below a single breakpoint (~768px) — same data, no columns dropped, just
  re-laid-out, since company/title/status/score are all needed on mobile
  too, not a "see more on desktop" degradation.
- The Opportunity Detail page's four panels (Job/Match/Contacts/Outreach)
  stack vertically at all widths below desktop; on desktop they may use a
  two-column layout. No panel's content changes by breakpoint, only the
  layout does.
- Forms (Preferences, Create Identity) are single-column at all widths —
  there's no multi-column form anywhere in this app, so nothing needs to
  reflow.

## Component primitives (owned by `frontend-design-agent`)

`Button` (primary/secondary/destructive variants), `Card`, `StatusBadge`
(the table above), `Table` (with the mobile card-collapse behavior),
`EmptyState` (icon + message + optional CTA — the single component reused
for every empty state documented per-page in [routes.md](routes.md)),
`ErrorState` (message + retry — the single component reused for every
page-level error), `Skeleton` (loading placeholders), `Modal`/`Dialog`
(Radix-backed), `Toaster` wiring (`sonner`, mounted once in `app/`).

No feature folder defines its own button, badge, or empty-state styling —
every visual primitive comes from this shared set, which is what keeps the
"consistent status badges," "obvious primary actions" direction actually
consistent across eight parallel implementation agents rather than
aspirational.

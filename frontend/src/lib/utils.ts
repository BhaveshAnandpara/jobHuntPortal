/**
 * `cn()` — the single class-name merge helper for this app, per
 * docs/frontend/frontend-revamp-spec.md T1 ("add `cn()` utility and
 * `class-variance-authority`/`tailwind-merge`").
 *
 * `clsx` resolves conditional/array/object class inputs; `twMerge` then
 * de-duplicates conflicting Tailwind utilities so a caller-supplied
 * `className` reliably wins over a component's own base classes (e.g.
 * passing `px-4` overrides a base `px-2.5` instead of both landing in the
 * class list and letting source order decide).
 *
 * Every generated primitive under `src/components/ui/` imports `cn` from
 * here, so there is exactly one merge implementation in the project.
 */

import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

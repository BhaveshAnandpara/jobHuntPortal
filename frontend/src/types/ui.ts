/**
 * UI-only types that are not backend DTOs — see
 * docs/frontend/state-management.md#type-strategy ("Hand-written frontend
 * DTOs" were rejected for backend data; this folder is for the opposite
 * case: types that only ever exist in the browser, e.g. a modal's local
 * step state). Every backend-shaped type lives in `src/api/types.ts`
 * instead, aliased from the generated OpenAPI schema — nothing here may
 * duplicate one of those.
 *
 * Empty at this skeleton step; feature agents add UI-only types here as
 * genuinely needed.
 */

export {}

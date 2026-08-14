# Repository Structure

```
frontend/
├── src/
│   ├── app/
│   │   ├── App.tsx                 # router mount, global providers
│   │   ├── router.tsx              # route table from routes.md
│   │   ├── providers.tsx           # QueryClientProvider, IdentityContext, Toaster
│   │   └── Layout.tsx              # nav, page shell
│   │
│   ├── api/
│   │   ├── generated/
│   │   │   └── schema.d.ts         # openapi-typescript output — regenerated, never hand-edited
│   │   ├── client.ts                # fetch wrapper, base URL, ApiError parsing
│   │   ├── users.ts
│   │   ├── resumes.ts
│   │   ├── profiles.ts
│   │   ├── jobs.ts
│   │   ├── matching.ts
│   │   ├── contacts.ts
│   │   ├── outreach.ts
│   │   ├── tracking.ts
│   │   └── queryKeys.ts             # the convention in state-management.md
│   │
│   ├── components/                  # shared, business-agnostic — design-system.md's primitive list
│   │   ├── Button.tsx
│   │   ├── Card.tsx
│   │   ├── StatusBadge.tsx
│   │   ├── Table.tsx
│   │   ├── EmptyState.tsx
│   │   ├── ErrorState.tsx
│   │   ├── Skeleton.tsx
│   │   ├── Modal.tsx
│   │   └── Toaster.tsx
│   │
│   ├── features/
│   │   ├── identity/                 # /welcome + IdentityContext consumer surface
│   │   ├── preferences/              # /settings
│   │   ├── resumes/                  # /resumes
│   │   ├── opportunities/            # / (dashboard pieces), /opportunities, /opportunities/:id
│   │   ├── contacts/                 # ContactsPanel — consumed inside opportunities' detail page
│   │   └── outreach/                 # /outreach, /outreach/:id
│   │
│   ├── hooks/                        # cross-cutting, not tied to one feature (useIdentity, usePolling helper)
│   ├── types/                        # UI-only types that aren't backend DTOs (e.g. StatusBadge category enum from design-system.md)
│   └── utils/                        # formatting, date helpers, base64 encode for resume upload
│
├── tests/
│   ├── mocks/
│   │   └── handlers.ts               # MSW handlers, per feature subfolder
│   └── e2e/                          # Playwright specs — testing-strategy.md's golden paths
│
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
└── .env.example                       # VITE_API_BASE_URL
```

## Ownership map (folder → agent)

| Folder | Owning agent |
|---|---|
| `app/` | `frontend-shell-agent` |
| `api/` (incl. `generated/`, `queryKeys.ts`) | `frontend-api-agent` |
| `components/` | `frontend-design-agent` |
| `features/identity/`, `features/preferences/`, `features/resumes/` | `frontend-profile-agent` |
| `features/opportunities/` | `frontend-opportunities-agent` |
| `features/contacts/` | `frontend-contacts-agent` |
| `features/outreach/` | `frontend-outreach-agent` |
| `hooks/`, `types/`, `utils/` | shared — whichever agent needs a cross-cutting helper adds it here, but the *first* agent to need something here checks it doesn't already exist rather than duplicating (mirrors the backend's `shared/` convention) |
| `tests/e2e/` | `integration-ui-agent` |
| `tests/mocks/handlers.ts` | shared, additive-only per feature subsection — each feature agent adds its own handlers, no agent edits another's |

Full input/output/dependency contracts per agent are in
[agent-ownership.md](agent-ownership.md) — this table is the folder-level
summary, that document is the enforceable boundary.

## Why `features/tracking/` doesn't exist as its own folder

The brief's example structure listed a separate `features/tracking/`. As
established in [routes.md](routes.md#why-this-differs-from-the-example-route-list-in-the-step-10-brief),
there is no backend data surface distinct from `GET /applications` for
"tracking" versus "opportunities" — they're the same list, same detail
entity, same lifecycle. A separate `tracking/` folder would either
duplicate `opportunities/`'s data-fetching logic or import across feature
folders (which [agent-ownership.md](agent-ownership.md) forbids for the
same reason `ownership.md` forbids it on the backend). The lifecycle
timeline and status-management UI live inside
`features/opportunities/` as part of the Opportunity Detail page.

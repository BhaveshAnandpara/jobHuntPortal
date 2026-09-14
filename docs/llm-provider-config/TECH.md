# Per-User LLM Provider Configuration: Technical Specification

_Status: Proposed · 2026-09-12_

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│  Frontend: Settings page                                               │
│  frontend/src/features/preferences/  (new Section, own save action)    │
│  - Provider dropdown (Claude / OpenAI / Groq / Gemini)                 │
│  - Optional model override, API key input (write-only)                │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ GET/PUT /users/me/llm-config (JWT bearer)
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  src/users/api/routes.py  — new routes                                 │
│  PUT validates live against the real provider BEFORE persisting        │
└───────────────────────────┬──────────────────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  user_llm_config table (new)               src/users/repository.py     │
│  provider | model | encrypted_api_key | last_validated_at | updated_at │
│  Fernet-encrypted at rest — key from LLM_CONFIG_ENCRYPTION_KEY env var │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ loaded by user_id (never a JWT dependency —
                            │ see below, most call sites have no request)
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  get_user_llm_client(user_id)   — new, in infrastructure/llm/          │
│  Replaces 4 module-level singletons + 1 uncached constructor:          │
│   - profiles/api/dependencies.py::get_llm_client                       │
│   - workflows/langgraph/job_matching/nodes.py::_get_llm_client         │
│   - workflows/langgraph/contact_discovery/nodes.py::_get_llm_client    │
│   - workflows/langgraph/outreach_generation/nodes.py::_get_llm_client  │
│   - jobs/ingestion/dependencies.py::get_structured_extractor           │
│  Raises LlmNotConfiguredError if no validated config exists.           │
└───────────────────────────┬──────────────────────────────────────────┘
                            ▼
        ┌───────────────────┴────────────────────┐
        │                                         │
   Synchronous (HTTP, JWT gives user_id)     Async (Kafka, user_id already
   - POST /resumes                            in the event payload)
   - POST /jobs/ingest-url                    - matching/consumers.py
   Both HARD-GATED: reject with 4xx             (NormalizedJob.user_id)
   before any pipeline work starts if no      - contacts/consumers.py
   validated config exists.                     (ContactSearchRequest.user_id)
                                               - outreach/consumers.py
                                                 (ContactRankingResult.user_id)
                                               Missing/bad key here is caught
                                               PER MESSAGE — marks that one
                                               opportunity "blocked", never
                                               crashes the consumer thread.

┌────────────────────────────────────────────────────────────────────────┐
│  New provider implementations (LLMProvider protocol, same shape as     │
│  existing groq/gemini/ollama providers):                               │
│  infrastructure/llm/anthropic_provider.py   (new dependency: anthropic)│
│  infrastructure/llm/openai_provider.py      (new dependency: openai)   │
└────────────────────────────────────────────────────────────────────────┘
```

**Why a new table, not new columns.** `scripts/bootstrap_db.py` runs `Base.metadata.create_all`, which issues `CREATE TABLE IF NOT EXISTS` — it never emits `ALTER TABLE` for a table that already has rows. A new `user_llm_config` table (mirroring `user_preferences`'s one-row-per-user shape) is created cleanly by a re-run of that script against an existing dev database; adding columns to `users` or `user_preferences` would require a manual `ALTER TABLE` this project has no tooling to generate.

**Why every call site already has `user_id` without new plumbing.** Confirmed by direct code inspection: the two synchronous HTTP paths already use `CurrentUserIdDependency` (JWT-derived); the three Kafka-triggered paths already carry `user_id` on their event payload/state (`NormalizedJob.user_id`, `ContactSearchRequest.user_id`, `ContactRankingResult.user_id`); the scheduled job-discovery path already iterates `JobSource` rows that carry `user_id`. No call site needs new context threaded in — only to use what's already there.

## Implementation Tasks

### Agent Assignment Summary

| Task | Suggested Agent | Why |
|---|---|---|
| 1. Encryption helper + dependency | general-purpose → ecc:security-reviewer | New crypto code needs a security pass before anything stores real keys against it |
| 2. `user_llm_config` table + domain type + repository | ecc:database-reviewer | Schema design, mirrors existing `user_preferences` pattern |
| 3. Anthropic provider | general-purpose → ecc:python-reviewer | New vendor SDK integration |
| 4. OpenAI provider | general-purpose → ecc:python-reviewer | Same |
| 5. `get_user_llm_client` factory + singleton replacement | ecc:python-reviewer | Touches 5 existing call sites across 4 subsystems — correctness-critical |
| 6. `GET/PUT /users/me/llm-config` routes | ecc:fastapi-reviewer | New endpoint, auth + validation patterns |
| 7. Gate `POST /resumes` / `POST /jobs/ingest-url` | ecc:fastapi-reviewer | Error-contract consistency with existing routes |
| 8. Kafka consumer resilience | ecc:python-reviewer → ecc:security-reviewer | Must guarantee one bad key can't affect other users |
| 9. Settings UI section | ecc:react-reviewer | Matches established Field/Section/Card conventions |
| 10. Frontend gating banners | ecc:react-reviewer | Reuses existing `jobSubmissionReadiness`-style pattern |
| 11. Runbook update | ecc:doc-updater | Documentation specialist |
| 12. Test suite | ecc:test-coverage | Cross-cutting — backend units, API tests, consumer test, frontend tests |

---

### Task 1: Encryption Helper

**Objective:** Add reversible encryption for API keys at rest — the one piece with no existing precedent in this codebase (only one-way bcrypt exists today, in `src/infrastructure/auth/passwords.py`).

**Acceptance Criteria:**
- [ ] `cryptography` added to `pyproject.toml` dependencies
- [ ] New module (e.g. `src/infrastructure/security/encryption.py`) exposing `encrypt(plaintext: str) -> str` / `decrypt(ciphertext: str) -> str`, backed by `cryptography.fernet.Fernet`
- [ ] Key sourced from a new required env var `LLM_CONFIG_ENCRYPTION_KEY` (documented in `.env.example` next to `JWT_SECRET_KEY`, with a generation one-liner: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
- [ ] Missing env var fails fast at startup with a clear message, same pattern as `JWT_SECRET_KEY`'s handling in `infrastructure/auth/config.py`
- [ ] Unit test: encrypt → decrypt round-trip recovers the original string; a tampered ciphertext raises rather than silently returning garbage

**Dependencies:** None
**Estimated effort:** 30 minutes

---

### Task 2: `user_llm_config` Table, Domain Type, Repository

**Objective:** Persist one LLM configuration per user, mirroring the existing `user_preferences` pattern exactly.

**Acceptance Criteria:**
- [ ] `UserLlmConfigRecord` added to `src/users/models.py`: `id` (PK), `user_id` (FK → `users.id`, unique + indexed), `provider` (Enum: `claude`/`openai`/`groq`/`gemini`), `model` (nullable string), `encrypted_api_key` (string), `last_validated_at` (nullable datetime), `updated_at`
- [ ] Domain type `src/shared/types/domain/user_llm_config.py` mirrors `user_preferences.py`'s shape, but never carries the decrypted key — only a `has_key: bool` / masked hint, consistent with the "keep off domain types" precedent in `passwords.py`
- [ ] `UserLlmConfigRepository` in `src/users/repository.py` with an `upsert(user_id, ...)` method mirroring `UserPreferencesRepository.upsert` (look up by `user_id`, update in place or insert)
- [ ] `scripts/bootstrap_db.py` re-run against the existing local dev DB creates the new table with zero manual SQL (verifies the "new table, not new columns" decision)
- [ ] Unit tests for the repository against SQLite, following this project's existing test-database convention

**Dependencies:** Task 1 (repository stores `encrypt()` output, never plaintext)
**Estimated effort:** 45 minutes

---

### Task 3: Anthropic Provider

**Objective:** Add Claude as a selectable provider.

**Acceptance Criteria:**
- [ ] `anthropic` added to `pyproject.toml` dependencies
- [ ] `src/infrastructure/llm/anthropic_provider.py` implements the `LLMProvider` protocol from `provider.py` (`name`, `complete(request: LLMRequest) -> LLMResponse`), constructed from an `LLMConfig` exactly like `GroqProvider`/`GeminiProvider`
- [ ] Vendor SDK exceptions normalized into `LLMProviderError` (`infrastructure/llm/errors.py`), matching existing provider error-handling
- [ ] Default model constant when no override is given (e.g. `claude-sonnet-4-5`) — confirm the exact current model id against Anthropic's docs at implementation time rather than hardcoding from this spec
- [ ] Unit tests with the vendor SDK mocked, same shape as existing `groq_provider` tests

**Dependencies:** None (parallel with Task 4)
**Estimated effort:** 45 minutes

---

### Task 4: OpenAI Provider

**Objective:** Add OpenAI as a selectable provider.

**Acceptance Criteria:** Same shape as Task 3, for `src/infrastructure/llm/openai_provider.py`, `openai` SDK dependency, default model (e.g. `gpt-4o-mini`).

**Dependencies:** None (parallel with Task 3)
**Estimated effort:** 45 minutes

---

### Task 5: `get_user_llm_client(user_id)` Factory

**Objective:** Replace the four module-level singleton caches and one uncached constructor with a single per-user factory, without changing any call site's surrounding logic beyond how it obtains its `LLMClient`.

**Acceptance Criteria:**
- [ ] New async function `get_user_llm_client(user_id: UserId) -> LLMClient` (suggested location: `src/infrastructure/llm/user_client.py`) — loads the `user_llm_config` row, decrypts the key (Task 1), builds an `LLMConfig`, returns `LLMClient(config=...)` using the existing `_default_provider`-style factory in `structured.py`, generalized to cover all four providers
- [ ] New `LlmNotConfiguredError` in `infrastructure/llm/errors.py`, raised when no row exists or `last_validated_at` is `None`
- [ ] All 5 existing call sites updated to call `get_user_llm_client(user_id)` with the `user_id` already available there, replacing their `_get_llm_client()`/`get_llm_client()`/fresh-`LLMClient()` patterns:
  - `src/profiles/api/dependencies.py`
  - `src/workflows/langgraph/job_matching/nodes.py`
  - `src/workflows/langgraph/contact_discovery/nodes.py`
  - `src/workflows/langgraph/outreach_generation/nodes.py`
  - `src/jobs/ingestion/dependencies.py`
- [ ] Existing `set_llm_client(...)` test-injection seams at each site are preserved or replaced with an equivalent per-user override seam, so existing tests that inject a fake client continue to work
- [ ] No call site changes its own business logic — this task is purely "how do I get my `LLMClient`", nothing else

**Dependencies:** Tasks 1, 2, 3, 4
**Estimated effort:** 1.5 hours (touches 5 files across 4 subsystems)

---

### Task 6: `GET/PUT /users/me/llm-config` Routes

**Objective:** Expose the config to the frontend, with live validation on write.

**Acceptance Criteria:**
- [ ] `GET /users/me/llm-config` in `src/users/api/routes.py`, `CurrentUserIdDependency` auth (same as `/users/me/preferences`), 404 if unset — same "unset is not an error" convention the frontend already expects
- [ ] Response schema (new `UserLlmConfigResponse` in `src/shared/types/api/users.py`): `provider`, `model`, masked key hint, `last_validated_at` — never the real key
- [ ] `PUT /users/me/llm-config` (new `UpdateUserLlmConfigRequest`: `provider`, `model?`, `api_key`): before persisting, builds a throwaway provider client with the submitted key and makes one minimal real call; on failure, returns `400 VALIDATION_ERROR` with a provider-specific message; on success, encrypts (Task 1) and upserts (Task 2) with `last_validated_at = now()`
- [ ] Full-replace semantics, consistent with the existing `/users/me/preferences` PUT
- [ ] API tests: valid key saves and is retrievable (masked); invalid key is rejected and nothing is persisted; missing auth returns 401

**Dependencies:** Tasks 2, 3, 4, 5
**Estimated effort:** 1 hour

---

### Task 7: Gate Resume Upload and Job Ingestion

**Objective:** Block the two synchronous LLM-triggering actions when the user has no validated config.

**Acceptance Criteria:**
- [ ] `POST /resumes` (`src/profiles/api/routes.py`) and `POST /jobs/ingest-url` (`src/jobs/ingestion/api.py`) check for a validated `user_llm_config` row before doing any work
- [ ] New `ErrorCode.LLM_NOT_CONFIGURED` mapped to a 4xx (align with this project's existing `{"code","message"}` error contract) with a message the frontend can render as "Add your AI provider key in Settings to use this feature"
- [ ] Both routes' existing behavior is otherwise unchanged when a valid config exists
- [ ] API tests: each route returns the new error with no config configured; each succeeds normally once one exists (mocking the provider call)

**Dependencies:** Task 5 (uses `get_user_llm_client`/a lighter existence check to avoid an extra live call on every submission — a cached `last_validated_at` check is enough here, not a fresh live call)
**Estimated effort:** 45 minutes

---

### Task 8: Kafka Consumer Resilience

**Objective:** Ensure a missing/invalid key for one user degrades gracefully for that one opportunity, and never crashes the shared consumer thread or blocks other users.

**Acceptance Criteria:**
- [ ] Before changing anything, confirm current behavior of `infrastructure/kafka/consumer.py`'s `EventConsumer`: does an unhandled exception from a `handle_*` function crash only that one message's processing, or the whole polling thread? (`run_consumers.py`'s `_run` only catches exceptions from `consumer.run()` at the thread level, which suggests today an unhandled exception inside a handler could take down that consumer's thread entirely — this must be fixed as part of this task if confirmed.)
- [ ] `matching/consumers.py::handle_job_discovered`, `contacts/consumers.py::handle_contacts_requested`, `outreach/consumers.py::handle_contacts_found` each specifically catch `LlmNotConfiguredError` (and provider-call failures surfaced as `LLMProviderError`), and instead of letting it propagate:
  - Log the event at `warning` level with `user_id` and `correlation_id`
  - Update the affected `Application` row's status/notes (via Tracking Service's existing update path) to reflect "blocked — reconfigure your AI key to continue," recorded in `application_history`
  - Return normally so the consumer's per-message loop continues to the next message
- [ ] A new test simulates: one user with no LLM config and one user with a valid one, both with jobs in flight through the same consumer — the first user's opportunity is marked blocked, the second user's proceeds normally, and the consumer thread is still alive and processing afterward
- [ ] If a new `ApplicationStatus` value is needed for the "blocked" state, add it to the existing enum and its manual-transition table (`_MANUAL_TRANSITIONS` in Tracking Service) so `PATCH /applications/{id}/status` behavior stays consistent

**Dependencies:** Task 5
**Estimated effort:** 1.5 hours (the exception-safety verification in the first bullet may surface pre-existing behavior worth fixing regardless of this feature)

---

### Task 9: Settings UI — AI Provider Section

**Objective:** Let users configure their provider and key, following this page's established conventions.

**Acceptance Criteria:**
- [ ] `frontend/src/api/llmConfig.ts`: `getLlmConfig`/`updateLlmConfig` functions + `useLlmConfig`/`useUpdateLlmConfig` TanStack Query hooks, mirroring `api/users.ts`'s existing shape
- [ ] New `Section`/`Card` in `SettingsPage.tsx` (or a sibling component it composes), with its own `zod` schema, its own `react-hook-form` instance, and its own save action — **not** folded into the existing `UpdateUserPreferencesRequest` form, per that page's documented "exactly six fields" invariant
- [ ] Provider `Select` (four options), optional model `Input`, API key `Input` (type `password`, write-only — never pre-filled with a real value, only ever shows the masked hint as placeholder text)
- [ ] `VALIDATION_ERROR` from the live-validation-on-save renders inline via the existing `serverError` banner pattern; success shows a toast, matching the rest of the page
- [ ] Component tests following the existing `SettingsPage.test.tsx` conventions

**Dependencies:** Task 6
**Estimated effort:** 1.5 hours

---

### Task 10: Frontend Gating Banners

**Objective:** Block the two gated actions in the UI with a clear path to Settings, before the user even tries and gets a 4xx.

**Acceptance Criteria:**
- [ ] Extend the existing readiness-check pattern (`jobSubmissionReadiness.ts`, built for the "no active profile" case) to also require a validated LLM config before allowing job submission on the Dashboard and Opportunities pages
- [ ] Add an equivalent check before the Resume upload action on `ResumesPage.tsx`
- [ ] Both reuse the existing `EmptyState`-with-CTA-to-Settings visual pattern already established for the zero-resume case — no new visual language invented
- [ ] Component tests: with no LLM config, both actions render the gated state instead of the normal form; with one configured, both render normally

**Dependencies:** Task 9 (needs the config query hook to exist)
**Estimated effort:** 1 hour

---

### Task 11: Runbook Update

**Objective:** Document the new secret before this ships anywhere beyond local dev.

**Acceptance Criteria:**
- [ ] `docs/deployment/RUNBOOK.md`'s secrets section lists `LLM_CONFIG_ENCRYPTION_KEY` alongside `JWT_SECRET_KEY`, with the same generation and rotation guidance
- [ ] `.env.example` updated with the new variable and its generation one-liner (Task 1)
- [ ] Note added that rotating this key invalidates every stored user LLM config (all users would need to reconfigure) — this is a real operational consequence, not just a formality

**Dependencies:** Task 1
**Estimated effort:** 20 minutes

---

### Task 12: Test Suite

**Objective:** Verify the feature end-to-end, per the Product Spec's Rollout & Validation Plan.

**Acceptance Criteria:**
- [ ] Backend unit tests: encryption round-trip (Task 1), each new provider with mocked SDK (Tasks 3–4), `get_user_llm_client` resolution and error cases (Task 5)
- [ ] API tests: `GET/PUT /users/me/llm-config` (Task 6), gating on `/resumes` and `/jobs/ingest-url` (Task 7)
- [ ] Consumer resilience test (Task 8) — the two-user scenario described there
- [ ] Frontend component tests for the new Settings section and both gating banners (Tasks 9–10)
- [ ] One extended e2e spec (`frontend/tests/e2e/`) covering: register → no LLM config → blocked on resume upload → configure a (fake/mocked) provider → resume upload succeeds
- [ ] Full existing suite (backend `pytest`, frontend `vitest`, Playwright) still passes — this feature must not regress anything already shipped

**Dependencies:** All prior tasks
**Estimated effort:** 2 hours

---

## Verification Checklist

- [ ] All 4 providers (Claude, OpenAI, Groq, Gemini) can be configured and validated with a real key
- [ ] Resume upload and job submission are blocked with the correct message when no key is configured, and succeed once one is
- [ ] `GET /users/me/llm-config` never returns a usable key in any response
- [ ] A key deleted mid-pipeline degrades gracefully — confirmed via Task 8's two-user test
- [ ] `LLM_CONFIG_ENCRYPTION_KEY` is documented in the runbook and `.env.example`
- [ ] Full backend and frontend test suites pass
- [ ] Manual walkthrough: at least one full pipeline run (resume → job → match → contact → outreach) completed per provider

## Timeline Estimate

- **Total effort:** ~12 hours across 12 tasks
- **Parallelizable:** Tasks 3 and 4 (the two new providers) can run in parallel; Task 1 has no dependencies and can start immediately
- **Critical path:** 1 → 2 → 5 → 6 → 7 → 8 → 9 → 10 → 12 (Tasks 3/4 feed into 5 but don't block 1/2)

## Rollback Plan

If validation surfaces a problem after partial rollout:

1. **Before any user has configured a key:** the gating in Task 7 can be feature-flagged off, reverting to the current global-`LLM_PROVIDER` behavior with no data loss
2. **After users have configured keys:** the `user_llm_config` table and its data are independent of the gating logic — gating can be disabled (falling back to the shared key temporarily) without losing anyone's stored configuration
3. **If `LLM_CONFIG_ENCRYPTION_KEY` is ever lost:** every stored key becomes undecryptable; the only recovery is asking all users to reconfigure. There is no way to avoid this after the fact, which is why Task 11's documentation matters before this ships

## References & Links

- Product spec: `docs/llm-provider-config/PRODUCT.md`
- Existing LLM abstraction: `src/infrastructure/llm/provider.py`, `structured.py`, `config.py`, `errors.py`
- Existing providers (pattern to follow): `src/infrastructure/llm/groq_provider.py`, `gemini_provider.py`
- Existing per-user data pattern (pattern to follow): `src/users/models.py`, `repository.py`, `service.py`, `api/routes.py` (`user_preferences`)
- Existing secret-handling precedent: `src/infrastructure/auth/passwords.py`, `config.py`
- Frontend conventions to follow: `frontend/src/features/preferences/SettingsPage.tsx`, `frontend/src/api/users.ts`, `frontend/src/features/opportunities/jobSubmissionReadiness.ts`
- Deployment secrets documentation to update: `docs/deployment/RUNBOOK.md`

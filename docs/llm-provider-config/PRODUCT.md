# Per-User LLM Provider Configuration: Product Specification

_Status: Proposed · 2026-09-12_

## Overview

Today every AI-powered action in JobHunt — resume parsing, job matching, contact discovery, outreach drafting, job extraction — runs against one shared, operator-configured LLM provider (`LLM_PROVIDER=groq` in `.env`). That's fine for a single operator running the app locally, but it breaks the moment the app is published for other people to use: every user's activity draws against the same rate-limited Groq key.

This feature lets each user configure their own LLM provider and API key — Claude, OpenAI, Groq, or Gemini — from Settings. Usage then scales with the number of users bringing their own credentials instead of hitting one shared quota, which is what makes publishing this app to other people sustainable.

**Target audience:** Any user of a publicly-hosted JobHunt instance, not just the operator.
**Success measure:** A new user can sign up, add their own LLM provider key in Settings, and use every AI-powered feature on their own quota — with zero draw on the operator's shared key.

## User Experience Goals

### For End Users

1. **Discoverability:** A clearly labeled "AI Provider" section in Settings, separate from search preferences.
2. **Clear gating:** Any AI-powered action attempted without a configured key is blocked immediately with a specific, actionable message — never a confusing failure discovered deep inside a pipeline minutes later.
3. **Confidence:** Saving a key validates it live against the real provider, so a typo or expired key is caught at save time, not on the next resume upload.
4. **Choice:** Works with whichever provider they already have a key for — Claude, OpenAI, Groq, or Gemini — plus an optional model override for anyone who wants a specific model.
5. **Privacy:** Their key is never echoed back in full once saved, and the decrypted value never leaves the backend.

### For the Operator (you)

1. **Sustainable publishing:** Publishing the app no longer means every user's AI usage counts against your own Groq quota.
2. **No secret sprawl:** No need to provision or rotate API keys on behalf of users.
3. **One new secret, clearly documented:** The single new operator-managed secret (the key that encrypts stored user keys) is documented in the deployment runbook at the same tier as `JWT_SECRET_KEY`.

## Behavior & Invariants

### Provider Choice & Configuration

- Users choose one of four providers: Claude (Anthropic), OpenAI, Groq, Gemini.
- An optional model override text field is available; leaving it unset uses a sensible per-provider default.
- Ollama is **not** offered as a user-selectable option — it requires a locally-running model server, which is meaningless for a user of a hosted, multi-tenant deployment.
- **Acceptance:** Settings shows a provider dropdown, an optional model field, and an API-key input. Saving with no provider selected is a validation error, shown inline.

### Key Validation

- On save, the backend makes one live, low-cost test call to the selected provider with the submitted key before persisting anything.
- **Acceptance:** An invalid or expired key is rejected with an inline error naming the problem (e.g., "This key was rejected by Groq — check it and try again"). A valid key saves successfully and records `last_validated_at`.

### Storage & Privacy

- The API key is encrypted at rest. The decrypted value is only ever materialized inside the one function that builds a provider client for an actual call — never placed on a general-purpose domain object, never returned by any API response.
- **Acceptance:** `GET /users/me/llm-config` returns `provider`, `model`, a masked hint (e.g. `sk-...ab12`), and `last_validated_at` — never the real key. No API response body, log line, or network trace ever contains a usable key.

### Gating AI-Powered Actions

- Uploading a resume (`POST /resumes`) and submitting a job URL (`POST /jobs/ingest-url`) — the two actions that synchronously trigger an LLM call — are blocked with a clear, specific error if the current user has no validated LLM configuration on file.
- **Acceptance:** Attempting either action with no key configured returns a 4xx whose message the frontend renders as "Add your AI provider key in Settings to use this feature," linking directly there. The same action succeeds immediately once a key is configured — no additional step required.

### Async Pipeline Resilience (the key-deleted-mid-flight case)

- Matching, contact discovery, and outreach generation all run asynchronously via Kafka after a job enters the pipeline. If a user's key becomes invalid or is deleted after that point, the affected step fails gracefully for that one opportunity: its status reflects that user action is needed ("blocked — reconfigure your AI key to continue"), visible in that opportunity's lifecycle timeline.
- **Acceptance:** One user's missing or bad key never stops, delays, or crashes processing for any other user's opportunities in the same Kafka consumer group.

### Provider Coverage

- Claude and OpenAI are newly supported providers — today only Groq, Gemini, and Ollama exist in the codebase.
- **Acceptance:** Choosing each of the four user-facing providers and completing a real AI-powered action (e.g. resume upload) succeeds end-to-end against that provider using that provider's own key.

## Success Criteria

1. ✓ A user can add a Claude, OpenAI, Groq, or Gemini key in Settings, with live validation on save
2. ✓ Resume upload and job submission are blocked with a clear, actionable message when no key is configured, and succeed immediately once one is
3. ✓ No API response, log, or client-visible artifact ever contains a usable API key after it's been saved
4. ✓ A key deleted mid-pipeline degrades one opportunity gracefully without affecting any other user
5. ✓ The operator's own Groq key sees zero traffic from other users' actions once this ships

## Non-Goals & Out of Scope

- Usage metering or a cost/billing dashboard for users — this is about credential ownership, not cost tracking
- A shared-key fallback or free trial quota — explicitly considered and rejected during design in favor of a hard requirement
- Ollama / self-hosted model servers as a user-selectable option
- Key rotation reminders or expiry notifications
- Admin visibility into what provider or key a given user has configured — the operator cannot see user keys, by design

## Risk Mitigation

### Encryption Key Loss

- **Risk:** losing `LLM_CONFIG_ENCRYPTION_KEY` makes every stored user key permanently unrecoverable (all users must reconfigure)
- **Mitigation:** documented in `docs/deployment/RUNBOOK.md` as a critical secret, same tier as `JWT_SECRET_KEY` — backed up the same way

### Wrong Key for Selected Provider

- **Risk:** a user pastes, say, an OpenAI key while "Claude" is selected
- **Mitigation:** live validation on save catches this immediately with a provider-specific error, before anything is stored

### Cost Surprise

- **Risk:** a user unfamiliar with metered LLM APIs is surprised by their own provider's bill
- **Mitigation:** Settings UI links to each provider's pricing page; in-app usage tracking is explicitly out of scope for this feature (see Non-Goals)

## Rollout & Validation Plan

1. Ship provider implementations, encrypted storage, gating, and the Settings UI together — there is no meaningful partial rollout, since the feature isn't usable until all pieces are present
2. Manual smoke test: configure each of the four providers with a real key, run one full pipeline (resume upload → job submit → match → contact discovery → outreach) per provider
3. Verify gating: attempt resume upload and job submission with no key configured, confirm the specific blocking message appears and links to Settings
4. Verify the mid-flight race: configure a key, submit a job, delete the key before matching completes, confirm the affected opportunity fails gracefully without crashing the consumer or affecting other users
5. Update `docs/deployment/RUNBOOK.md` to document `LLM_CONFIG_ENCRYPTION_KEY` before this ships to the beta deployment

## References

- Existing LLM provider abstraction: `src/infrastructure/llm/provider.py`, `structured.py`, `config.py`
- Existing per-provider implementations: `src/infrastructure/llm/{groq,gemini,ollama}_provider.py`
- Existing Settings page conventions: `frontend/src/features/preferences/SettingsPage.tsx`
- Existing secret-handling precedent (one-way, for contrast): `src/infrastructure/auth/passwords.py`
- Deployment secrets documentation: `docs/deployment/PRODUCT.md`, `docs/deployment/TECH.md`
- Technical design: `docs/llm-provider-config/TECH.md`
